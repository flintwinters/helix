"""Repeatable CLI speed measurements, with semantic checks outside timed regions."""
import hashlib
import json
import os
import platform
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

import yaml


SAMPLES = 5
WARMUPS = 1


def repeated(expression, expected, count, bindings):
    # A wrong result jumps past the target, so correctness fails without an infinite loop.
    return (f"[let, {json.dumps(bindings, ensure_ascii=False)}, [until, [fn, [n], [ge, n, {count}]], "
            f"[fn, [n], [add, n, [if, [eq, {expression}, {expected}], 1, {count + 1}]]], 0]]")


def workloads():
    yield "arithmetic-map", "".join(f"v{i}: [add, {i}, 1]\n" for i in range(1000)), {f"v{i}": i + 1 for i in range(1000)}, 1000
    yield "function-loop", repeated("[sub, [add, n, 1], n]", "1", 2000, {}), 2000, 2000
    for name, text in (("slice-ascii", "abcdefgh" * 1000), ("slice-unicode", "é雪🙂a" * 2000)):
        yield name, repeated("[slice, text, 3999, 4003]", json.dumps(text[3999:4003], ensure_ascii=False), 200, {"text": text}), 200, 200
    key = "lookup-key-" * 400
    yield "map-get-long-key", repeated("[get, mapping, key]", "42", 1000, {"mapping": {key: 42}, "key": key}), 1000, 1000
    yield "map-set-existing-key", repeated("[get, [set, mapping, key, 43], key]", "43", 1000,
                                           {"mapping": {key: 42}, "key": key}), 1000, 1000


def verify(h):
    base = h.ROOT / "runtime" / "speed"
    base.mkdir(parents=True, exist_ok=True)
    artifact = Path(tempfile.mkdtemp(dir=base, prefix="run-"))
    report = {
        "schema_version": 1,
        "environment": {"platform": platform.platform(), "machine": platform.machine(),
                        "python": platform.python_version(), "profile": "debug",
                        "binary_sha256": hashlib.sha256(h.BIN.read_bytes()).hexdigest()},
        "method": {"clock": "perf_counter_ns", "samples": SAMPLES, "warmups": WARMUPS,
                   "scope": "CLI process startup, file loading, evaluation and state emission; excludes build and Python YAML decoding",
                   "policy": "correctness and subprocess timeout are enforced; timings are observations, not portable pass/fail thresholds"},
        "run_directory": str(artifact), "measurements": [],
    }
    try:
        for name, source, expected, operations in workloads():
            program = artifact / f"{name}.yaml"
            program.write_text(source)
            samples = []
            measurement = {"workload": name, "operations_per_run": operations, "seconds": samples}
            report["measurements"].append(measurement)
            for index in range(WARMUPS + SAMPLES):
                started = time.perf_counter_ns()
                result = subprocess.run([str(h.BIN), "run", str(program)], cwd=h.ROOT,
                                        capture_output=True, text=True,
                                        timeout=float(os.environ.get("HELIX_TEST_TIMEOUT", "10")))
                elapsed = (time.perf_counter_ns() - started) / 1e9
                (artifact / f"{name}.state.yaml").write_text(result.stdout)
                (artifact / f"{name}.stderr").write_text(result.stderr)
                assert result.returncode == 0, (name, result.returncode, result.stdout, result.stderr)
                state = yaml.safe_load(result.stdout)
                assert state == {"status": "finished", "root": expected}, (name, state)
                if index >= WARMUPS:
                    samples.append(elapsed)
            median = statistics.median(samples)
            measurement.update(median_seconds=median, min_seconds=min(samples), max_seconds=max(samples),
                               operations_per_second=operations / median)
            print(f"speed: {name}: median {median:.6f}s, range {min(samples):.6f}..{max(samples):.6f}s ({operations / median:.0f} operations/s)", flush=True)
    finally:
        content = json.dumps(report, indent=2) + "\n"
        (artifact / "report.json").write_text(content)
        (base / "report.json").write_text(content)
        print(f"speed: report and replay inputs/states: {artifact}", flush=True)
