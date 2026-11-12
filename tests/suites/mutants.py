"""Compile deliberate faults in an isolated workspace and demand real suites reject them."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path

from tests.suites import contracts

MUTATIONS = [
    ("arithmetic", "src/builtins/number.rs", '"add" => acc.checked_add(x)',
     '"add" => acc.checked_sub(x)', "generated"),
    ("source-blame", "src/graph.rs", 'col: m.col() + 1', 'col: m.col() + 2', "parser"),
    ("serialization", "src/emit.rs", 'Self::value(v, i == 0)', 'Self::value(v, false)', "golden"),
]


def verify(h):
    base = h.root / "runtime" / "mutants"
    base.mkdir(exist_ok=True)
    run_directory = Path(tempfile.mkdtemp(dir=base, prefix="run-"))
    workspace = run_directory / "workspace"
    workspace.mkdir()
    # Include compile-time assets as well as Rust and standard-library sources.
    for name in ("src", "std", "editor", "completions"):
        shutil.copytree(h.root / name, workspace / name)
    for name in ("Cargo.toml", "Cargo.lock"):
        shutil.copy2(h.root / name, workspace / name)
    target = run_directory / "target"
    mutant = replace(h, binary=target / "debug" / "helix")
    env = {**os.environ, "CARGO_TARGET_DIR": str(target)}
    reports = []
    def build(label):
        result = subprocess.run(["cargo", "build", "--offline", "--quiet"], cwd=workspace,
                                env=env, text=True, capture_output=True, timeout=120)
        (run_directory / f"{label}.build.log").write_text(result.stdout + result.stderr)
        assert result.returncode == 0, f"{label}: build failed, not a killed mutation; see runtime/mutants/"
    def run(suite):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            if suite == "golden":
                failed = mutant.regression(False, [suite]) != 0
            else:
                failures = contracts.run(suite, mutant)
                output.write("\n".join(failures))
                failed = bool(failures)
        return failed, output.getvalue()
    try:
        build("control")
        for suite in {mutation[-1] for mutation in MUTATIONS}:
            failed, log = run(suite)
            (run_directory / f"control-{suite}.log").write_text(log)
            assert not failed, f"unmodified {suite} fails; cannot assess mutation sensitivity"
        for label, filename, before, after, suite in MUTATIONS:
            path = workspace / filename
            original = path.read_text()
            assert original.count(before) == 1, f"refresh mutation anchor: {label} in {filename}"
            try:
                path.write_text(original.replace(before, after))
                build(label)
                failed, log = run(suite)
                (run_directory / f"{label}.{suite}.log").write_text(log)
                reports.append({"mutation": label, "file": filename, "suite": suite, "killed": failed})
                assert failed, f"{label} survived {suite}; see runtime/mutants/"
                print(f"mutants: {suite} rejected compiled {label} fault")
            finally:
                path.write_text(original)
                # Rebuild the control between mutations so faults cannot accidentally combine.
                build("restored")
    finally:
        (base / "report.json").write_text(json.dumps({"run_directory": str(run_directory), "mutations": reports}, indent=2) + "\n")
        if len(reports) == len(MUTATIONS) and all(row["killed"] for row in reports):
            shutil.rmtree(target)
