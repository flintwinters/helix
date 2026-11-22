"""Repeatable CLI speed measurements, with semantic checks outside timed regions."""
import hashlib
import json
import os
import platform
import re
import shutil
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

import yaml


SAMPLES = 5
WARMUPS = 1
# Names the Cargo dev profile measured (Cargo.toml: opt-level 1, dependencies 3).
# Changing it separates history and pinned baselines from other build profiles.
PROFILE = "dev-opt1"
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


# Load per CPU above which wall time says more about the host than the program.
BUSY_LOAD = 0.5
# Counted instructions are deterministic (perf varies ~0.1%, callgrind ~0), so a small margin is a regression.
INSTRUCTION_MARGIN = 1.02


def compatible_history(base, environment, name, source_hash, field, counter=None):
    """Values of `field` from the earliest five compatible runs, avoiding a drifting baseline.
    Counted values compare only with the same counter: perf and callgrind totals differ."""
    values = []
    reports = {path.parent: path for path in base.glob("run-*/report.json")}
    reports.update((path.parent, path) for path in base.glob("run-*/report.yaml"))
    for path in sorted(reports.values(), key=lambda p: p.stat().st_mtime):
        try:
            previous = yaml.safe_load(path.read_text())
            if previous.get("comparison_environment") != environment:
                continue
            for row in previous["measurements"]:
                if (row["workload"] == name and row.get("source_sha256") == source_hash
                        and row.get("baseline_epoch", 1) == BASELINE_EPOCHS.get(name, 1)
                        and (counter is None or row.get("counter") == counter)):
                    values.append(row[field])
        except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
            continue
        if len(values) >= 5:
            break
    return values


def history_comparison(base, environment, name, source_hash, samples, loaded=False):
    """Wall-time comparison; a loaded host reports the change but never flags it."""
    medians = compatible_history(base, environment, name, source_hash, "median_seconds")
    if not medians:
        return {"status": "no baseline"}
    baseline = statistics.median(medians)
    current = statistics.median(samples)
    # Require both a practical slowdown and separation from observed timing noise.
    noise = max(statistics.median(abs(x - baseline) for x in medians),
                statistics.median(abs(x - current) for x in samples))
    flagged = current > baseline * 1.20 and current - baseline > 3 * noise
    status = "ok" if loaded or not flagged else "SLOWDOWN"
    if loaded and flagged:
        status = "unreliable: host busy"
    return {"status": status, "baseline_runs": len(medians),
            "baseline_seconds": baseline, "change_percent": (current / baseline - 1) * 100}


def instruction_comparison(base, environment, name, source_hash, counter, counted):
    """Compare counted instructions with the earliest compatible runs; load cannot inflate them."""
    history = compatible_history(base, environment, name, source_hash, "median_instructions", counter)
    if not history:
        return {"status": "no baseline", "unit": "instructions", "counter": counter}
    baseline = statistics.median(history)
    return {"status": "SLOWDOWN" if counted > baseline * INSTRUCTION_MARGIN else "ok",
            "baseline_runs": len(history), "baseline_instructions": baseline,
            "change_percent": (counted / baseline - 1) * 100, "unit": "instructions", "counter": counter}


def pinned_limit(baseline, environment, name, source_hash):
    """The pinned median instruction count for a matching workload and environment, if any.
    A pin whose workload source changed would otherwise silently stop enforcing anything."""
    if baseline["comparison_environment"] != environment:
        return None
    for row in baseline["measurements"]:
        if row["workload"] == name:
            assert row["source_sha256"] == source_hash, (
                f"pinned workload `{name}` changed source (pinned {row['source_sha256']}, now {source_hash}); "
                "re-measure it and update tests/fixtures/speed-baseline.json")
            return row["median_instructions"]
    return None


def pinned_comparison(limit, median):
    # Retired user-space instructions, unlike wall or CPU time, do not inflate on a busy host.
    return {"status": "SLOWDOWN" if median > limit else "ok", "baseline_runs": 1,
            "baseline_instructions": limit, "change_percent": (median / limit - 1) * 100,
            "unit": "instructions", "enforced": True}


def counter_name():
    """The deterministic instruction counter available here: `perf` when installed, else
    callgrind, else none (wall time alone is not trustworthy on a shared host)."""
    return "perf" if shutil.which("perf") else "callgrind" if shutil.which("valgrind") else None


def instructions(h, program, record, counter):
    """Retired user-space instructions of one run: `perf stat` or callgrind, written to `record`."""
    if counter == "callgrind":
        # Emulated and ~50x slower than native, but exact and independent of host load.
        result = subprocess.run(["valgrind", "--tool=callgrind", f"--callgrind-out-file={record}.out",
                                 str(h.binary), "run", str(program)],
                                cwd=h.root, capture_output=True, text=True, timeout=h.timeout * 100)
        assert result.returncode == 0, (program.name, result.returncode, result.stderr[-500:])
        found = re.search(r"refs:\s+([\d,]+)", result.stderr)
        assert found, f"callgrind reported no instruction total for {program.name}"
        Path(f"{record}.out").unlink(missing_ok=True)
        return int(found.group(1).replace(",", ""))
    try:
        result = subprocess.run(["perf", "stat", "-x,", "-e", "instructions:u", "-o", str(record), "--",
                                 str(h.binary), "run", str(program)],
                                cwd=h.root, capture_output=True, text=True, timeout=h.timeout)
    except FileNotFoundError:
        raise AssertionError("pinned speed workloads count instructions with `perf`, which is not installed; "
                             "install linux-tools for this kernel") from None
    assert result.returncode == 0, (program.name, result.returncode, result.stdout, result.stderr)
    for line in record.read_text().splitlines():
        count, _, event = (line.split(",") + ["", ""])[:3]
        if event.startswith("instructions") and count.isdigit():
            return int(count)
    raise AssertionError(
        f"`perf stat` could not count instructions:u ({record}); allow user-space counting with "
        "`sudo sysctl kernel.perf_event_paranoid=2` (persist it in /etc/sysctl.d/)")


def host_load():
    return os.getloadavg()[0] / (os.cpu_count() or 1)


def verify(h):
    base = h.root / "runtime" / "speed"
    base.mkdir(parents=True, exist_ok=True)
    verify_comparison(base)
    artifact = Path(tempfile.mkdtemp(dir=base, prefix="run-"))
    policy_source = (h.root / "tooling" / "test_policy.yaml").read_bytes()
    (artifact / "test_policy.source.yaml").write_bytes(policy_source)
    counter = counter_name()
    report = {
        "schema_version": 2,
        "environment": {"platform": platform.platform(), "machine": platform.machine(),
                        "python": platform.python_version(), "profile": PROFILE,
                        "binary_sha256": hashlib.sha256(h.binary.read_bytes()).hexdigest()},
        # File imports are mutable independently of the executable. Preserve their
        # provenance without excluding implementation changes from comparisons.
        "imported_sources": {"tooling/test_policy.yaml": {
            "sha256": hashlib.sha256(policy_source).hexdigest(), "artifact": "test_policy.source.yaml"}},
        "method": {"clock": "perf_counter_ns", "samples": SAMPLES, "warmups": WARMUPS,
                   "scope": "CLI process startup, file loading, evaluation and state emission; excludes build and Python YAML decoding",
                   "policy": "correctness, subprocess timeout, and compatible pinned median instruction counts are enforced; "
                             "counted instructions are the regression signal, wall time is informational",
                   "counter": counter, "counter_note": "perf stat instructions:u, else callgrind; separate untimed runs"},
        "run_directory": str(artifact), "measurements": [],
    }
    report["comparison_environment"] = {
        "platform": platform.platform(), "host": platform.node(),
        "machine": platform.machine(), "processor": platform.processor(),
        "profile": PROFILE, "samples": SAMPLES, "warmups": WARMUPS,
    }
    baseline = json.loads((h.root / "tests" / "fixtures" / "speed-baseline.json").read_text())
    slowdowns = []
    report["host_load_start"] = host_load()
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
                                        capture_output=True, text=True, timeout=h.timeout)
                elapsed = (time.perf_counter_ns() - started) / 1e9
                (artifact / f"{name}.state.yaml").write_text(result.stdout)
                (artifact / f"{name}.stderr").write_text(result.stderr)
                assert result.returncode == 0, (name, result.returncode, result.stdout, result.stderr)
                state = yaml.safe_load(result.stdout)
                assert state.get("status") == "finished" and state.get("root") == expected, (
                    name, "workload result differs from independent oracle", state.get("status"))
                if index >= WARMUPS:
                    samples.append(elapsed)
            source_hash = hashlib.sha256(source.encode()).hexdigest()
            median = statistics.median(samples)
            environment = report["comparison_environment"]
            limit = pinned_limit(baseline, environment, name, source_hash)
            loaded = host_load() > BUSY_LOAD
            if counter:
                # Counted separately so the counter never enters the timed samples. Callgrind is
                # exact, so one run suffices; perf varies slightly, so it takes the median.
                runs = SAMPLES if counter == "perf" else 1
                counts = [instructions(h, program, artifact / f"{name}.count.{index}.csv", counter) for index in range(runs)]
                counted = statistics.median(counts)
                measurement.update(counter=counter, instructions=counts, median_instructions=counted)
                if limit is not None and counter == "perf":
                    comparison = pinned_comparison(limit, counted)
                    if comparison["status"] == "SLOWDOWN":
                        slowdowns.append(f"{name}: {counted:.0f} instructions > {limit}")
                else:
                    comparison = instruction_comparison(base, environment, name, source_hash, counter, counted)
            else:
                comparison = history_comparison(base, environment, name, source_hash, samples, loaded)
            measurement["host_busy"] = loaded or host_load() > BUSY_LOAD
            measurement.update(source_sha256=source_hash, comparison=comparison)
            detail = comparison["status"]
            if "change_percent" in comparison:
                detail += (f": {comparison['change_percent']:+.1f}% {comparison.get('unit', 'time')} vs baseline"
                           f" ({comparison['baseline_runs']} runs)")
            print(f"speed: {name}: {detail}", flush=True)
            measurement.update(median_seconds=median, min_seconds=min(samples), max_seconds=max(samples),
                               operations_per_second=operations / median)
            print(f"speed: {name}: median {median:.6f}s, range {min(samples):.6f}..{max(samples):.6f}s ({operations / median:.0f} operations/s)", flush=True)
    finally:
        report["host_load_end"] = host_load()
        content = yaml.safe_dump(report, sort_keys=False)
        (artifact / "report.yaml").write_text(content)
        (base / "report.yaml").write_text(content)
        print(f"speed: report and replay inputs/states: {artifact}", flush=True)
    if slowdowns:
        raise AssertionError("pinned speed baseline exceeded: " + ", ".join(slowdowns))


def verify_comparison(base):
    """Deterministic policy contracts, using isolated project-local history."""
    pinned = {"comparison_environment": {}, "measurements": [
        {"workload": "w", "source_sha256": "hash", "median_instructions": 1000}]}
    assert pinned_limit(pinned, {}, "w", "hash") == 1000
    for median, status in ((999, "ok"), (1000, "ok"), (1001, "SLOWDOWN")):
        assert pinned_comparison(1000, median)["status"] == status
    assert pinned_limit(pinned, {"host": "other"}, "w", "hash") is None
    assert pinned_limit(pinned, {}, "other", "hash") is None
    try:
        pinned_limit(pinned, {}, "w", "changed")
    except AssertionError as error:
        assert "re-measure" in str(error)
    else:
        raise AssertionError("a stale pinned source hash must fail")
    with tempfile.TemporaryDirectory(dir=base, prefix="policy-") as directory:
        root = Path(directory)
        assert history_comparison(root, {}, "w", "hash", [1] * 5)["status"] == "no baseline"
        assert instruction_comparison(root, {}, "w", "hash", "callgrind", 1000)["status"] == "no baseline"
        counted = root / "run-counted"
        counted.mkdir()
        (counted / "report.yaml").write_text(yaml.safe_dump({
            "comparison_environment": {}, "measurements": [
                {"workload": "w", "source_sha256": "hash", "counter": "callgrind", "median_instructions": 1000}]}))
        for count, status in ((1000, "ok"), (1020, "ok"), (1021, "SLOWDOWN")):
            assert instruction_comparison(root, {}, "w", "hash", "callgrind", count)["status"] == status
        # perf and callgrind totals are different units: a counter never compares with the other.
        assert instruction_comparison(root, {}, "w", "hash", "perf", 5000)["status"] == "no baseline"
        run = root / "run-baseline"
        run.mkdir()
        (run / "report.yaml").write_text(yaml.safe_dump({
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
        path = run / "report.yaml"
        path.write_text(yaml.safe_dump(report))
        assert history_comparison(root, {}, "slice-ascii", "hash", [2] * 5)["status"] == "no baseline"
        report["measurements"][0]["baseline_epoch"] = BASELINE_EPOCHS["slice-ascii"]
        path.write_text(yaml.safe_dump(report))
        assert history_comparison(root, {}, "slice-ascii", "hash", [2] * 5)["status"] == "SLOWDOWN"
        # A loaded host's slowdown is reported but never flagged: load inflates every sample alike.
        assert history_comparison(root, {}, "slice-ascii", "hash", [2] * 5, loaded=True)["status"] == "unreliable: host busy"
