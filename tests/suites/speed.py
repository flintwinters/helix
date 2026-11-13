"""Repeatable CLI speed measurements, with semantic checks outside timed regions."""
import hashlib
import json
import platform
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

import yaml


SAMPLES = 5
WARMUPS = 1
# Advance only after a verified optimization warrants a new fixed reference.
# Missing epochs in historical reports belong to the original baseline.
BASELINE_EPOCHS = {"slice-ascii": 3, "slice-unicode": 3}


def repeated(expression, expected, count, bindings):
    # A wrong result jumps past the target, so correctness fails without an infinite loop.
    return (f'{{"$call": ["let", {json.dumps(bindings, ensure_ascii=False)}, {{"$call": ["until", {{"$call": ["fn", ["n"], {{"$call": ["ge", {{"$ref": "n"}}, {count}]}}]}}, {{"$call": ["fn", ["n"], {{"$call": ["add", {{"$ref": "n"}}, {{"$call": ["if", {{"$call": ["eq", {expression}, {expected}]}}, 1, {count + 1}]}}]}}]}}, 0]}}]}}')


def workloads():
    yield "arithmetic-map", json.dumps({f"v{i}": {"$call": ["add", i, 1]} for i in range(1000)}), {f"v{i}": i + 1 for i in range(1000)}, 1000
    yield "function-loop", repeated('{"$call": ["sub", {"$call": ["add", {"$ref": "n"}, 1]}, {"$ref": "n"}]}', "1", 2000, {}), 2000, 2000
    values = {f"value{i}": i for i in range(100)}
    emitted = "".join(f"value{i}: {i}\n" for i in range(100))
    yield "yaml-dump-map", repeated('{"$call": ["len", {"$call": ["yaml_dump", {"$ref": "value"}]}]}', str(len(emitted)), 200,
                                     {"value": values}), 200, 200
    for name, text in (("slice-ascii", "abcdefgh" * 1000), ("slice-unicode", "é雪🙂a" * 2000)):
        yield name, repeated('{"$call": ["slice", {"$ref": "text"}, 3999, 4003]}', json.dumps(text[3999:4003], ensure_ascii=False), 200, {"text": text}), 200, 200
    key = "lookup-key-" * 400
    yield "map-get-long-key", repeated('{"$call": ["get", {"$ref": "mapping"}, {"$ref": "key"}]}', "42", 1000, {"mapping": {key: 42}, "key": key}), 1000, 1000
    yield "map-set-existing-key", repeated('{"$call": ["get", {"$call": ["set", {"$ref": "mapping"}, {"$ref": "key"}, 43]}, {"$ref": "key"}]}', "43", 1000,
                                           {"mapping": {key: 42}, "key": key}), 1000, 1000

    from tests.suites.mutation_generation import reference_plan
    # Fixed seam/Unicode input and an explicitly chosen real robustness fixture.
    # Do not dynamically choose the largest case: source hashes should change
    # only when the chosen workload changes, not when unrelated fixtures grow.
    unicode_source = ('a' * 255 + '\r\n' + ''.join(
        f'field_{index}: "雪🙂é e\u0301 ' + 'payload ' * 6 + '"\r\n'
        for index in range(96)) + 'last: "tail"\u2028')
    fixture = Path(__file__).resolve().parents[2] / 'tests' / 'cases' / 'dogfood_testing.yaml'
    for name, text in (('fuzz-plan-unicode-crlf', unicode_source),
                       ('fuzz-plan-dogfood-testing', fixture.read_text())):
        source = {'$call': ['let', {'fuzz': {'$call': ['import', 'std:fuzz']}},
                            {'$call': ['fuzz.plan', text]}]}
        yield name, json.dumps(source, ensure_ascii=False), reference_plan(text), len(text)

    fields = {f"field_{index:03}": "integer" for index in range(256)}
    source = {"$call": ["let", {"types": {"$call": ["import", "std:types"]}},
                        {"$call": ["types.from_fields", fields]}]}
    yield "types-from-fields-256", json.dumps(source), {"type": "record", "fields": fields}, len(fields)

    # Exercise reconstruction of concrete standard-library machine declarations. The
    # oracle is ordinary data, independent of the runtime's realization helpers.
    machine_fields = {
        "hostname": "string",
        "packages": {"type": "default", "of": {"type": "list", "of": "string"}, "default": []},
        "files": {"type": "default", "of": {"type": "map", "of": "string"}, "default": {}},
        "services": {"type": "default", "of": {"type": "map", "of": {
            "type": "enum", "values": ["running", "stopped"]}}, "default": {}},
        "users": {"type": "default", "of": {"type": "list", "of": "string"}, "default": []},
    }
    fleet_size = 16
    declarations, expected_fleet = [], []
    for index in range(fleet_size):
        name = f"node-{index:02}"
        fixed = {"hostname": f"{name}.example", "packages": ["nginx", "curl"]}
        declarations.append({"$call": ["inherit", name,
            [{"$ref": "machine.machine"}], fixed]})
        provenance = {field: {"type": "host" if field == "hostname" else field,
            "value": name if field in fixed else f"default of {field}",
            "value_kind": "explicit" if field in fixed else "default"}
            for field in machine_fields}
        expected_fleet.append({"type": "class", "name": name, "parents": ["machine"],
            "fields": machine_fields, "values": {**fixed, "files": {}, "services": {}, "users": []},
            "provenance": provenance, "concrete": True})
    source = {"$call": ["let", {"types": {"$call": ["import", "std:types"]},
        "machine": {"$call": ["import", "std:machine"]}},
        {"$call": ["map", {"$call": ["fn", ["declaration"],
            {"$call": ["types.redeclare", {"$ref": "declaration"},
                [{"$ref": "machine.machine"}]]}]}, declarations]}]}
    yield "types-redeclare-machine-fleet", json.dumps(source), expected_fleet, fleet_size

    # Start with a persisted ordinary descriptor so timing isolates redeclaration
    # from constructing the same original fields through from_fields first.
    own_fields, own_values, own_provenance = {}, {}, {}
    declaration_name = "service-settings"
    for index in range(128):
        field = f"setting_{index:03}"
        origin = {"type": declaration_name}
        if index % 3 == 0:
            own_fields[field] = "integer"
            own_values[field] = index
            origin.update(value=declaration_name, value_kind="explicit")
        elif index % 3 == 1:
            own_fields[field] = {"type": "optional", "of": "string"}
        else:
            own_fields[field] = {"type": "default", "of": "string", "default": f"default-{index}"}
            own_values[field] = f"default-{index}"
            origin.update(value=f"default of {declaration_name}", value_kind="default")
        own_provenance[field] = origin
    own_declaration = {"type": "class", "name": declaration_name, "parents": [],
        "fields": own_fields, "values": own_values, "provenance": own_provenance, "concrete": True}
    source = {"$call": ["let", {"types": {"$call": ["import", "std:types"]}},
        {"$call": ["types.redeclare", {"$literal": own_declaration}, []]}]}
    yield "types-redeclare-own-fields-128", json.dumps(source), own_declaration, len(own_fields)

    # Use the actual manage.py policy and catalog, retaining process logs rather
    # than replacing production report assembly with a small arithmetic proxy.
    from tooling.cli import SUITES

    observations = [{"suite": name, "failures": [], "stdout": "retained log\n" * 256, "stderr": ""}
                    for name in SUITES]
    assert len(observations) >= 2, "report workload requires failed and skipped controls"
    observations[0]["failures"] = ["observed process failure"]
    observations[1]["reason"] = "explicitly skipped workload"
    failure_heavy = [{"suite": name, "failures": [
        f'{name} case {index}: expected deterministic result; observed mismatch\n'
        + ('captured source, expected value, actual value, and replay context\n' * 16)
        for index in range(4)], "stdout": "retained log\n" * 256, "stderr": ""} for name in SUITES]
    for name, rows in (("test-policy-report", observations), ("test-policy-report-failures", failure_heavy)):
        results = [{**row, "status": "skipped" if "reason" in row else "failed" if row["failures"] else "passed"}
                   for row in rows]
        failure_count = sum(len(row["failures"]) for row in rows)
        expected = {"command": "test", "status": "failed" if failure_count else "passed",
                    "exit_code": int(bool(failure_count)), "failure_count": failure_count, "results": results}
        # Programs live in runtime/speed/run-*; this import preserves replayability
        # without making source hashes depend on the absolute checkout directory.
        source = {"$call": ["let", {"policy": {"$call": ["import", "../../../tooling/test_policy.yaml"]}},
                            {"$call": ["policy.report", {"$literal": rows}]}]}
        yield name, json.dumps(source), expected, len(rows)


def history_comparison(base, environment, name, source_hash, samples):
    """Compare against the earliest five compatible runs, avoiding a drifting baseline."""
    medians = []
    for path in sorted(base.glob("run-*/report.json"), key=lambda p: p.stat().st_mtime):
        try:
            previous = json.loads(path.read_text())
            if previous.get("comparison_environment") != environment:
                continue
            for row in previous["measurements"]:
                if (row["workload"] == name and row.get("source_sha256") == source_hash
                        and row.get("baseline_epoch", 1) == BASELINE_EPOCHS.get(name, 1)):
                    medians.append(row["median_seconds"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if len(medians) >= 5:
            break
    if not medians:
        return {"status": "no baseline"}
    baseline = statistics.median(medians)
    current = statistics.median(samples)
    # Require both a practical slowdown and separation from observed timing noise.
    noise = max(statistics.median(abs(x - baseline) for x in medians),
                statistics.median(abs(x - current) for x in samples))
    flagged = current > baseline * 1.20 and current - baseline > 3 * noise
    return {"status": "SLOWDOWN" if flagged else "ok", "baseline_runs": len(medians),
            "baseline_seconds": baseline, "change_percent": (current / baseline - 1) * 100}


def verify(h):
    base = h.root / "runtime" / "speed"
    base.mkdir(parents=True, exist_ok=True)
    verify_comparison(base)
    artifact = Path(tempfile.mkdtemp(dir=base, prefix="run-"))
    policy_source = (h.root / "tooling" / "test_policy.yaml").read_bytes()
    (artifact / "test_policy.source.yaml").write_bytes(policy_source)
    report = {
        "schema_version": 2,
        "environment": {"platform": platform.platform(), "machine": platform.machine(),
                        "python": platform.python_version(), "profile": "debug",
                        "binary_sha256": hashlib.sha256(h.binary.read_bytes()).hexdigest()},
        # File imports are mutable independently of the executable. Preserve their
        # provenance without excluding implementation changes from comparisons.
        "imported_sources": {"tooling/test_policy.yaml": {
            "sha256": hashlib.sha256(policy_source).hexdigest(), "artifact": "test_policy.source.yaml"}},
        "method": {"clock": "perf_counter_ns", "samples": SAMPLES, "warmups": WARMUPS,
                   "scope": "CLI process startup, file loading, evaluation and state emission; excludes build and Python YAML decoding",
                   "policy": "correctness and subprocess timeout are enforced; timings are observations, not portable pass/fail thresholds"},
        "run_directory": str(artifact), "measurements": [],
    }
    report["comparison_environment"] = {
        "platform": platform.platform(), "host": platform.node(),
        "machine": platform.machine(), "processor": platform.processor(),
        "profile": "debug", "samples": SAMPLES, "warmups": WARMUPS,
    }
    try:
        for name, source, expected, operations in workloads():
            program = artifact / f"{name}.yaml"
            program.write_text(source)
            samples = []
            measurement = {"workload": name, "baseline_epoch": BASELINE_EPOCHS.get(name, 1),
                           "operations_per_run": operations, "seconds": samples}
            if name in {"types-from-fields-256", "test-policy-report", "test-policy-report-failures"}:
                measurement["operation_unit"] = "fields" if name == "types-from-fields-256" else "suite observations"
            elif name.startswith("fuzz-plan-"):
                measurement["operation_unit"] = "source Unicode scalars"
            elif name == "types-redeclare-machine-fleet":
                measurement["operation_unit"] = "machine declarations"
            elif name == "types-redeclare-own-fields-128":
                measurement["operation_unit"] = "own field declarations"
            report["measurements"].append(measurement)
            for index in range(WARMUPS + SAMPLES):
                started = time.perf_counter_ns()
                result = subprocess.run([str(h.binary), "run", str(program)], cwd=h.root,
                                        capture_output=True, text=True,
                                        timeout=h.timeout)
                elapsed = (time.perf_counter_ns() - started) / 1e9
                (artifact / f"{name}.state.yaml").write_text(result.stdout)
                (artifact / f"{name}.stderr").write_text(result.stderr)
                assert result.returncode == 0, (name, result.returncode, result.stdout, result.stderr)
                state = yaml.safe_load(result.stdout)
                assert state == {"status": "finished", "root": expected}, (name, state)
                if index >= WARMUPS:
                    samples.append(elapsed)
            source_hash = hashlib.sha256(source.encode()).hexdigest()
            comparison = history_comparison(base, report["comparison_environment"], name, source_hash, samples)
            measurement.update(source_sha256=source_hash, comparison=comparison)
            detail = comparison["status"]
            if "change_percent" in comparison:
                detail += f": {comparison['change_percent']:+.1f}% time vs baseline ({comparison['baseline_runs']} runs)"
            print(f"speed: {name}: {detail}", flush=True)
            median = statistics.median(samples)
            measurement.update(median_seconds=median, min_seconds=min(samples), max_seconds=max(samples),
                               operations_per_second=operations / median)
            print(f"speed: {name}: median {median:.6f}s, range {min(samples):.6f}..{max(samples):.6f}s ({operations / median:.0f} operations/s)", flush=True)
    finally:
        content = json.dumps(report, indent=2) + "\n"
        (artifact / "report.json").write_text(content)
        (base / "report.json").write_text(content)
        print(f"speed: report and replay inputs/states: {artifact}", flush=True)


def verify_comparison(base):
    """Deterministic policy contracts, using isolated project-local history."""
    with tempfile.TemporaryDirectory(dir=base, prefix="policy-") as directory:
        root = Path(directory)
        assert history_comparison(root, {}, "w", "hash", [1] * 5)["status"] == "no baseline"
        run = root / "run-baseline"
        run.mkdir()
        (run / "report.json").write_text(json.dumps({
            "comparison_environment": {}, "measurements": [
                {"workload": "w", "source_sha256": "hash", "median_seconds": 1}]}))
        for samples, expected in [([1.1] * 5, "ok"), ([1.5] * 5, "SLOWDOWN"),
                                  ([0.5, 1, 1.5, 2, 2.5], "ok")]:
            assert history_comparison(root, {}, "w", "hash", samples)["status"] == expected
        assert history_comparison(root, {"host": "other"}, "w", "hash", [2] * 5)["status"] == "no baseline"
        assert history_comparison(root, {}, "w", "changed", [2] * 5)["status"] == "no baseline"
        # Old slice reports remain readable but cannot hide a post-optimization regression.
        report = {"comparison_environment": {}, "measurements": [
            {"workload": "slice-ascii", "source_sha256": "hash", "median_seconds": 1}]}
        path = run / "report.json"
        path.write_text(json.dumps(report))
        assert history_comparison(root, {}, "slice-ascii", "hash", [2] * 5)["status"] == "no baseline"
        report["measurements"][0]["baseline_epoch"] = BASELINE_EPOCHS["slice-ascii"]
        path.write_text(json.dumps(report))
        assert history_comparison(root, {}, "slice-ascii", "hash", [2] * 5)["status"] == "SLOWDOWN"
