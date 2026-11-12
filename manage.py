#!/usr/bin/env python3
"""Single entrypoint for Helix; use --help or --help-json to discover commands."""
import sys
import os
from pathlib import Path

from tooling.cli import DEFAULT_TEST_JOBS, SUITES, parse
from tooling.output import DEFAULT_FORMAT, emit

ROOT = Path(__file__).resolve().parent
BIN = ROOT / os.environ.get("CARGO_TARGET_DIR", "target") / "debug" / "helix"
HYPERLINTER = ROOT / "hyperlinter"


def build(*, report=False, output_format=DEFAULT_FORMAT) -> int:
    import subprocess

    try:
        result = subprocess.run(["cargo", "build", "--quiet"], cwd=ROOT, capture_output=True, text=True)
    except OSError as error:
        result = subprocess.CompletedProcess(["cargo", "build", "--quiet"], 2, "", str(error))
    if output_format == "text":
        if report:
            print(f"build: {'failed' if result.returncode else 'passed'} (exit {result.returncode})")
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
    elif report or result.returncode:
        emit({"command": "build", "status": "failed" if result.returncode else "passed",
              "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr},
             stream=None if report else sys.stderr)
    elif result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    return result.returncode


def lint(extra: list[str]) -> int:
    """Run hyperlinter from the host root (required so it loads this repo's Cargo.toml and baseline)."""
    import subprocess

    tsx = HYPERLINTER / "node_modules" / ".bin" / "tsx"
    if not tsx.exists():
        subprocess.check_call(["git", "submodule", "update", "--init", "hyperlinter"], cwd=ROOT)
        subprocess.check_call(["npm", "--prefix", str(HYPERLINTER), "ci", "--include=dev"], cwd=ROOT)
    return subprocess.call([str(tsx), str(HYPERLINTER / "src" / "cli.ts"), *extra], cwd=ROOT)


def test_context():
    from tooling.test_runner import TestContext

    return TestContext(ROOT, BIN, timeout=float(os.environ.get("HELIX_TEST_TIMEOUT", "10")))


def test(bless: bool, suites: list[str] | None = None, jobs: int = DEFAULT_TEST_JOBS,
         patterns: list[str] | None = None, output_format: str = DEFAULT_FORMAT) -> int:
    from dataclasses import replace
    from tooling.test_runner import discover_cases

    context = test_context()
    try:
        cases = discover_cases(context.commands, context.root, patterns) if patterns else None
    except ValueError as error:
        if output_format == "text":
            print(f"error: {error}", file=sys.stderr)
        else:
            emit({"command": "test", "status": "error", "exit_code": 2,
                  "error": {"kind": "selection", "message": str(error)}}, stream=sys.stderr)
        return 2
    if suites and set(suites) == {"startup"}:
        return context.regression(bless, suites, jobs, output_format=output_format)

    import shutil
    import tempfile

    if (code := build(output_format=output_format)) != 0:
        return code
    (ROOT / "runtime").mkdir(exist_ok=True)
    # Freeze this run's executable so another build cannot replace it mid-suite.
    with tempfile.TemporaryDirectory(dir=ROOT / "runtime", prefix="test-binary-") as directory:
        snapshot = Path(directory) / "helix"
        shutil.copy2(context.binary, snapshot)
        return replace(context, binary=snapshot).regression(bless, suites, jobs, cases, output_format)


def main() -> int:
    args = parse(sys.argv[1:])
    if args is None:
        return 0
    command = args.command
    if command == "test" and (args.list or args.describe):
        if args.format == "json":
            emit({"command": "test", "status": "ok", "suites": SUITES if args.describe else list(SUITES)})
        else:
            for name, description in SUITES.items():
                print(f"{name}: {description}" if args.describe else name)
        return 0
    # Discovery must respond before loading process execution and test machinery.
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
    if command == "exec":
        return build() or subprocess.call([str(BIN), "exec", *([args.output_format] if args.output_format else []), "--", args.source], cwd=ROOT)
    if command == "inventory":
        from tooling.inventory import inventory
        return build() or inventory(test_context().helix, ROOT)
    if command == "build":
        return build(report=True, output_format=args.format)
    if command in ("run", "recover", "resume", "check"):
        return build() or subprocess.call([str(BIN), command, args.file], cwd=ROOT)
    if command == "sandbox":
        return build() or subprocess.call([str(BIN), "sandbox", args.root, args.file], cwd=ROOT)
    if command == "std":
        if args.module is None and args.format == "json":
            if (code := build()) != 0:
                return code
            result = subprocess.run([str(BIN), "std"], cwd=ROOT, capture_output=True, text=True)
            emit({"command": "std", "status": "failed" if result.returncode else "ok",
                  "exit_code": result.returncode, "modules": result.stdout.splitlines(), "stderr": result.stderr})
            return result.returncode
        return build() or subprocess.call([str(BIN), "std", *([args.module] if args.module else [])], cwd=ROOT)
    return test(args.bless, args.suite, args.jobs, args.case, args.format)


if __name__ == "__main__":
    sys.exit(main())
