"""Compile deliberate faults in an isolated workspace and demand real suites reject them."""
import contextlib
import io
import json
import os
import shutil
import subprocess
from unittest.mock import patch

from tests.suites import contracts

MUTATIONS = [
    ("arithmetic", "src/builtins/number.rs", '"add" => acc.checked_add(x)',
     '"add" => acc.checked_sub(x)', "generated"),
    ("source-blame", "src/graph.rs", 'col: m.col() + 1', 'col: m.col() + 2', "parser"),
    ("serialization", "src/emit.rs", 'string(s, position == 0)', 'string(s, false)', "golden"),
]


def verify(h):
    base = h.ROOT / "runtime" / "mutants"
    workspace = base / "workspace"
    base.mkdir(exist_ok=True)
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir()
    for name in ("src", "std"):
        shutil.copytree(h.ROOT / name, workspace / name)
    for name in ("Cargo.toml", "Cargo.lock"):
        shutil.copy2(h.ROOT / name, workspace / name)
    target = base / "target"
    env = {**os.environ, "CARGO_TARGET_DIR": str(target)}
    reports = []
    def build(label):
        result = subprocess.run(["cargo", "build", "--offline", "--quiet"], cwd=workspace,
                                env=env, text=True, capture_output=True, timeout=120)
        (base / f"{label}.build.log").write_text(result.stdout + result.stderr)
        assert result.returncode == 0, f"{label}: build failed, not a killed mutation; see runtime/mutants/"
    def run(suite):
        output = io.StringIO()
        with patch.object(h, "BIN", target / "debug" / "helix"), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            if suite == "golden":
                failed = h.regression(False, [suite]) != 0
            else:
                failures = contracts.run(suite, h)
                output.write("\n".join(failures))
                failed = bool(failures)
        return failed, output.getvalue()
    try:
        build("control")
        for suite in {mutation[-1] for mutation in MUTATIONS}:
            failed, log = run(suite)
            (base / f"control-{suite}.log").write_text(log)
            assert not failed, f"unmodified {suite} fails; cannot assess mutation sensitivity"
        for label, filename, before, after, suite in MUTATIONS:
            path = workspace / filename
            original = path.read_text()
            assert original.count(before) == 1, f"refresh mutation anchor: {label} in {filename}"
            try:
                path.write_text(original.replace(before, after))
                build(label)
                failed, log = run(suite)
                (base / f"{label}.{suite}.log").write_text(log)
                reports.append({"mutation": label, "file": filename, "suite": suite, "killed": failed})
                assert failed, f"{label} survived {suite}; see runtime/mutants/"
                print(f"mutants: {suite} rejected compiled {label} fault")
            finally:
                path.write_text(original)
                # Rebuild the control between mutations so faults cannot accidentally combine.
                build("restored")
    finally:
        (base / "report.json").write_text(json.dumps(reports, indent=2) + "\n")
