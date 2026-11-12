"""Linux peak-RSS regression workloads; GNU time measures only the interpreter child."""
import json
import os
import platform
import subprocess
import time
from pathlib import Path

import yaml


def verify(h):
    if platform.system() != "Linux" or not Path("/usr/bin/time").exists():
        raise AssertionError("memory suite requires Linux and GNU /usr/bin/time; select other suites on other hosts")
    artifact = h.ROOT / "runtime" / "memory"
    artifact.mkdir(exist_ok=True)
    measurements = []
    def measure(name, source):
        program = artifact / f"{name}.yaml"
        program.write_text(source)
        peak = artifact / f"{name}.rss"
        with (artifact / f"{name}.state.yaml").open("w") as output:
            started = time.perf_counter()
            result = subprocess.run(["/usr/bin/time", "-f", "%M", "-o", str(peak),
                                     str(h.BIN), "run", str(program)], cwd=h.ROOT,
                                    stdout=output, stderr=subprocess.PIPE, text=True,
                                    timeout=float(os.environ.get("HELIX_TEST_TIMEOUT", "10")))
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
            rss, elapsed = measure(f"map-{count}", "".join(f"v{i}: [add, {i}, 1]\n" for i in range(count)))
            assert elapsed < 2, (count, elapsed)
            peaks.append(rss)
        assert peaks[-1] <= peaks[0] * 12 + 16 * 1024, peaks
        # Identical live state, 100x more work: detect accidental per-iteration retention.
        short, _ = measure("loop-999", '[until, [fn, [n], [eq, n, 999]], [fn, [n], [add, n, 1]], 0]')
        long, _ = measure("loop-99999", '[until, [fn, [n], [eq, n, 99999]], [fn, [n], [add, n, 1]], 0]')
        assert long <= short + 8 * 1024, (short, long, "constant-state loop retained memory proportional to steps")
    finally:
        report = {"environment": {"platform": platform.platform(), "python": platform.python_version(),
                                   "binary": str(h.BIN), "profile": "debug", "rss": "GNU time max RSS, KiB"},
                  "measurements": measurements}
        (artifact / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        for measurement in measurements:
            print(f"memory: {measurement}")
        print("memory: replay inputs, states and environment in runtime/memory/")
