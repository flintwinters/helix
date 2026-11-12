"""Linux peak-RSS regression workloads; GNU time measures only the interpreter child."""
import json
import platform
import subprocess
import time
import tempfile
from pathlib import Path

import yaml


def verify(h):
    if platform.system() != "Linux" or not Path("/usr/bin/time").exists():
        raise AssertionError("memory suite requires Linux and GNU /usr/bin/time; select other suites on other hosts")
    base = h.root / "runtime" / "memory"
    base.mkdir(exist_ok=True)
    artifact = Path(tempfile.mkdtemp(dir=base, prefix="run-"))
    measurements = []
    def measure(name, source):
        program = artifact / f"{name}.yaml"
        program.write_text(source)
        peak = artifact / f"{name}.rss"
        with (artifact / f"{name}.state.yaml").open("w") as output:
            started = time.perf_counter()
            result = subprocess.run(["/usr/bin/time", "-f", "%M", "-o", str(peak),
                                     str(h.binary), "run", str(program)], cwd=h.root,
                                    stdout=output, stderr=subprocess.PIPE, text=True,
                                    timeout=h.timeout)
            elapsed = time.perf_counter() - started
        state = yaml.safe_load((artifact / f"{name}.state.yaml").read_text())
        assert result.returncode == 0 and state["status"] == "finished", (name, result, state)
        rss = int(peak.read_text())
        assert 0 < rss < 192 * 1024, (name, rss, "peak RSS exceeds 192 MiB")
        measurements.append({"workload": name, "seconds": elapsed, "peak_kib": rss})
        return rss, elapsed
    try:
        peaks = []
        for count in (250, 1000, 2500):
            rss, elapsed = measure(f"map-{count}", json.dumps({f"v{i}": {"$call": ["add", i, 1]} for i in range(count)}))
            assert elapsed < 2, (count, elapsed)
            peaks.append(rss)
        assert peaks[-1] <= peaks[0] * 12 + 16 * 1024, peaks
        # Identical live state, 100x more work: detect accidental per-iteration retention.
        short, _ = measure("loop-999", '{"$call": ["until", {"$call": ["fn", ["n"], {"$call": ["eq", {"$ref": "n"}, 999]}]}, {"$call": ["fn", ["n"], {"$call": ["add", {"$ref": "n"}, 1]}]}, 0]}')
        long, _ = measure("loop-99999", '{"$call": ["until", {"$call": ["fn", ["n"], {"$call": ["eq", {"$ref": "n"}, 99999]}]}, {"$call": ["fn", ["n"], {"$call": ["add", {"$ref": "n"}, 1]}]}, 0]}')
        assert long <= short + 8 * 1024, (short, long, "constant-state loop retained memory proportional to steps")
    finally:
        report = {"environment": {"platform": platform.platform(), "python": platform.python_version(),
                                   "binary": str(h.binary), "profile": "debug", "rss": "GNU time max RSS, KiB"},
                  "measurements": measurements}
        report["run_directory"] = str(artifact)
        (artifact / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        (base / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        for measurement in measurements:
            print(f"memory: {measurement}")
        print("memory: replay inputs, states and environment in runtime/memory/")
