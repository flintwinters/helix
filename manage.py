#!/usr/bin/env python3
"""Single entrypoint for Helix; use --help or --help-json to discover commands."""
import sys
import os
from pathlib import Path

from tooling.cli import SUITES, parse
from tooling.test_reports import failure as test_failure

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "tests" / "cases"
CHECK_CASES = ROOT / "tests" / "check"
COMMANDS = {CASES: "run", CHECK_CASES: "check"}  # case directory -> helix subcommand
BIN = ROOT / os.environ.get("CARGO_TARGET_DIR", "target") / "debug" / "helix"
HYPERLINTER = ROOT / "hyperlinter"


def build() -> int:
    return subprocess.call(["cargo", "build", "--quiet"], cwd=ROOT)


def lint(extra: list[str]) -> int:
    """Run hyperlinter from the host root (required so it loads this repo's Cargo.toml and baseline)."""
    tsx = HYPERLINTER / "node_modules" / ".bin" / "tsx"
    if not tsx.exists():
        subprocess.check_call(["git", "submodule", "update", "--init", "hyperlinter"], cwd=ROOT)
        subprocess.check_call(["npm", "--prefix", str(HYPERLINTER), "ci", "--include=dev"], cwd=ROOT)
    return subprocess.call([str(tsx), str(HYPERLINTER / "src" / "cli.ts"), *extra], cwd=ROOT)


def helix(*args: str, stdin: str | None = None) -> "subprocess.CompletedProcess":
    try:
        return subprocess.run([str(BIN), *args], input=stdin, capture_output=True, text=True, cwd=ROOT,
                              timeout=float(os.environ.get("HELIX_TEST_TIMEOUT", "10")))
    except subprocess.TimeoutExpired as error:
        return subprocess.CompletedProcess([str(BIN), *args], 124, "", f"timed out: {error}")


def root_section(state: str) -> str | None:
    """Dedented body of the `root:` block of a finished state, or None."""
    lines = state.splitlines()
    if not lines or lines[0] != "status: finished":
        return None
    body = []
    for line in lines[lines.index("root:") + 1 :]:
        if not line.startswith("  "):
            break
        body.append(line[2:])
    return "\n".join(body) + "\n"


ERROR_FIELDS = {"kind", "message", "line", "column"}  # `path` is omitted for errors outside the graph (parse)


def malformed_state(out: str, returncode: int) -> str | None:
    """Why `out` is not a well-formed serialized state (None if it is): the contract that nothing panics
    and every error is structured, machine-parseable data."""
    import yaml

    if returncode not in (0, 1):
        return f"exit code {returncode} (panic?)"
    try:
        state = yaml.safe_load(out)
    except yaml.YAMLError as e:
        return f"state is not valid YAML: {e}"
    if not isinstance(state, dict) or "status" not in state:
        return "state has no `status`"
    if state["status"] not in ("finished", "error"):
        return f"unexpected terminal status {state['status']!r}"
    if (state["status"] == "error") != (returncode == 1):
        return "exit code disagrees with status"
    if state["status"] == "finished" and "root" not in state:
        return "finished state has no root"
    if state["status"] == "error":
        errors = state.get("errors") if "errors" in state else [state.get("error")]
        if not (isinstance(errors, list) and errors and all(isinstance(e, dict) and ERROR_FIELDS <= e.keys() for e in errors)):
            return f"error lacks fields {sorted(ERROR_FIELDS)}"
    return None


def mutations(src: str):
    """Damaged variants of a program: every line prefix, every single-line deletion, and strided character prefixes."""
    lines = src.splitlines(keepends=True)
    for n in range(len(lines)):
        yield "".join(lines[:n])
        yield "".join(lines[:n] + lines[n + 1 :])
    for n in range(0, len(src), 5):
        yield src[:n]


def robustness(cases: list[Path]) -> list[str]:
    failures = []
    for case in cases:
        src = case.read_text()
        command = COMMANDS[case.parent]
        for variant in [src, *mutations(src)]:
            run = helix(command, "-", stdin=variant)
            if (why := malformed_state(run.stdout, run.returncode)) is not None:
                failures.append(test_failure(ROOT, case, "robustness", why, command, run, source=variant))
                break
    return failures


def test(bless: bool, suites: list[str] | None = None) -> int:
    if suites and set(suites) == {"startup"}:
        return regression(bless, suites)

    import shutil
    import tempfile

    global BIN
    if (code := build()) != 0:
        return code
    (ROOT / "runtime").mkdir(exist_ok=True)
    # Freeze this run's executable so another build cannot replace it mid-suite.
    with tempfile.TemporaryDirectory(dir=ROOT / "runtime", prefix="test-binary-") as directory:
        original = BIN
        snapshot = Path(directory) / "helix"
        shutil.copy2(original, snapshot)
        shutil.copy2(original.with_name("hx"), snapshot.with_name("hx"))
        BIN = snapshot
        try:
            return regression(bless, suites)
        finally:
            BIN = original


def regression(bless: bool, suites: list[str] | None) -> int:
    selected = set(suites or SUITES)
    failures = []
    for name in (name for name in SUITES if name in selected - {"golden", "robustness"}):
        if name == "library":
            result = subprocess.run(["cargo", "test", "--quiet"], cwd=ROOT, timeout=120)
            if result.returncode:
                failures.append("library: cargo test failed")
        else:
            from tests.suites import contracts
            failures.extend(contracts.run(name, sys.modules[__name__]))
    if not selected & {"golden", "robustness"}:
        for failure in failures:
            print("FAIL", failure, file=sys.stderr)
        return int(bool(failures))
    cases = [c for d in COMMANDS for c in sorted(d.glob("*.yaml")) if not c.name.endswith(".expected.yaml")]
    modules = helix("std")
    expected_modules = {f"std:{source.stem}" for source in (ROOT / "std").glob("*.yaml")}
    discovered_modules = modules.stdout.splitlines()
    if modules.returncode != 0 or set(discovered_modules) != expected_modules or len(discovered_modules) != len(expected_modules):
        failures.append("standard library discovery failed")
    for source in sorted((ROOT / "std").glob("*.yaml")):
        shown = helix("std", source.stem)
        if shown.returncode != 0 or shown.stdout != source.read_text():
            failures.append(f"{source.name}: standard library source differs")
    if helix("std", "unknown").returncode != 2:
        failures.append("unknown standard library must exit with usage error")
    for case in cases if "golden" in selected else []:
        expected_path = case.with_suffix(".expected.yaml")
        command = COMMANDS[case.parent]
        run = helix(command, str(case.relative_to(ROOT)))
        out = run.stdout
        if (why := malformed_state(out, run.returncode)) is not None:
            failures.append(test_failure(ROOT, case, "state", why, command, run))
            continue
        repeated = helix(command, str(case.relative_to(ROOT)))
        if (repeated.returncode, repeated.stdout, repeated.stderr) != (run.returncode, out, run.stderr):
            failures.append(test_failure(ROOT, case, "determinism", "two runs differ", command, repeated, expected=out))
            continue
        if bless:
            expected_path.write_text(out)
        elif not expected_path.exists() or expected_path.read_text() != out:
            failures.append(test_failure(ROOT, case, "golden", f"state differs from {expected_path.relative_to(ROOT)}",
                                         command, run, expected=expected_path.read_text() if expected_path.exists() else ""))
            continue
        if (program := root_section(out)) is not None:
            reloaded = helix("run", "-", stdin=program)
            if root_section(reloaded.stdout) != program or reloaded.returncode != 0:
                failures.append(test_failure(ROOT, case, "roundtrip", "emitted root is not stable",
                                             "run", reloaded, source=program, expected=out))
    if not bless and "robustness" in selected:
        failures += robustness(cases)
    for f in failures:
        print("FAIL", f, file=sys.stderr)
    print(f"{len(cases)} golden inputs; {len(failures)} failures")
    return 1 if failures else 0


def main() -> int:
    args = parse(sys.argv[1:])
    if args is None:
        return 0
    command = args.command
    if command == "test" and (args.list or args.describe):
        for name, description in SUITES.items():
            print(f"{name}: {description}" if args.describe else name)
        return 0
    # Discovery must respond before loading process execution and test machinery.
    global subprocess
    import subprocess

    if command == "lint":
        return lint(args.extra)
    if command == "cli":
        return build() or subprocess.call([str(BIN), *args.extra], cwd=ROOT)
    if command == "eval":
        if (code := build()) != 0:
            return code
        return subprocess.run([str(BIN), "check" if args.check else "run", "-"],
                              input=args.source, text=True, cwd=ROOT).returncode
    if command == "inventory":
        from tooling.inventory import inventory
        return build() or inventory(helix, ROOT)
    if command == "build":
        return build()
    if command in ("run", "recover", "resume", "check"):
        return build() or subprocess.call([str(BIN), command, args.file], cwd=ROOT)
    if command == "std":
        return build() or subprocess.call([str(BIN), "std", *([args.module] if args.module else [])], cwd=ROOT)
    return test(args.bless, args.suite)


if __name__ == "__main__":
    sys.exit(main())
