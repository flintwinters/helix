#!/usr/bin/env python3
"""Single entrypoint for Helix; use --help or --help-json to discover commands."""
import sys
import os
from pathlib import Path
from contextlib import contextmanager

from tooling.cli import DEFAULT_TEST_JOBS, SUITES, parse
from tooling.output import DEFAULT_FORMAT, emit

ROOT = Path(__file__).resolve().parent
BIN = ROOT / os.environ.get("CARGO_TARGET_DIR", "target") / "debug" / "helix"
HYPERLINTER = ROOT / "hyperlinter"


def _build_unlocked(*, report=False, output_format=DEFAULT_FORMAT) -> int:
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


@contextmanager
def build_lock():
    """Serialize project wrappers through build and executable snapshot creation."""
    import fcntl

    (ROOT / "runtime").mkdir(exist_ok=True)
    with (ROOT / "runtime" / "build.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def build(*, report=False, output_format=DEFAULT_FORMAT) -> int:
    with build_lock():
        return _build_unlocked(report=report, output_format=output_format)


def lint(extra: list[str]) -> int:
    """Run hyperlinter from the host root (required so it loads this repo's Cargo.toml and baseline)."""
    import subprocess

    tsx = HYPERLINTER / "node_modules" / ".bin" / "tsx"
    if not tsx.exists():
        subprocess.check_call(["git", "submodule", "update", "--init", "hyperlinter"], cwd=ROOT)
        subprocess.check_call(["npm", "--prefix", str(HYPERLINTER), "ci", "--include=dev"], cwd=ROOT)
    scratch = ROOT / "runtime" / "tsx"
    scratch.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.setdefault("TMPDIR", str(scratch))
    return subprocess.call([str(tsx), str(HYPERLINTER / "src" / "cli.ts"), *extra], cwd=ROOT, env=environment)


def test_context():
    from tooling.test_runner import TestContext

    return TestContext(ROOT, BIN, timeout=float(os.environ.get("HELIX_TEST_TIMEOUT", "10")))


@contextmanager
def frozen_binary(binary, output_format=DEFAULT_FORMAT):
    """Build and snapshot atomically; keep execution independent of later builds."""
    import shutil
    import tempfile

    (ROOT / "runtime").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / "runtime", prefix="execution-binary-") as directory:
        snapshot = Path(directory) / "helix"
        with build_lock():
            code = _build_unlocked(output_format=output_format)
            if code == 0:
                shutil.copy2(binary, snapshot)
        yield (snapshot if code == 0 else None), code


@contextmanager
def frozen_test_context(context, output_format=DEFAULT_FORMAT):
    """Retain test configuration while sharing the runtime executable lifecycle."""
    from dataclasses import replace

    with frozen_binary(context.binary, output_format) as (binary, code):
        yield (replace(context, binary=binary) if code == 0 else None), code


def test(bless: bool, suites: list[str] | None = None, jobs: int = DEFAULT_TEST_JOBS,
         patterns: list[str] | None = None, output_format: str = DEFAULT_FORMAT) -> int:
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

    with frozen_test_context(context, output_format) as (frozen, code):
        return code or frozen.regression(bless, suites, jobs, cases, output_format)


def main() -> int:
    args = parse(sys.argv[1:])
    if args is None:
        return 0
    command = args.command
    if command == "docker-nginx":
        from tests.suites.docker_nginx import dispatch
        return dispatch(ROOT, args.action, args.run_id, args.format)
    if command == "test" and (args.list or args.describe):
        if args.format == "json":
            emit({"command": "test", "status": "ok", "suites": SUITES if args.describe else list(SUITES)})
        else:
            for name, description in SUITES.items():
                print(f"{name}: {description}" if args.describe else name)
        return 0
    if command == "migrate-syntax":
        from tooling.syntax_migration import migrate_files
        results = migrate_files([Path(path) for path in args.paths], write=args.write)
        if args.format == "json":
            emit({"command": command, "status": "ok", "written": args.write, "files": results})
        else:
            for result in results:
                print(f"{result['status']}: {result['path']}" + (f": {result['reason']}" if "reason" in result else ""))
        return 2 if any(result["status"] == "error" for result in results) else 0
    # Discovery and bootstrap commands stay independent of Helix execution.

    if command == "migration-audit":
        from tooling.syntax_migration import audit_golden
        with frozen_test_context(test_context(), args.format) as (frozen, code):
            return code or audit_golden(frozen, args.baseline, args.format)
    if command == "lint":
        return lint(args.extra)
    if command == "build":
        return build(report=True, output_format=args.format)
    if command == "test":
        return test(args.bless, args.suite, args.jobs, args.case, args.format)
    with frozen_binary(BIN) as (binary, code):
        return code or runtime(args, binary)


def perform_forwarded(binary, arguments):
    """Forward controller cancellation and wait for the adapter's descendant cleanup."""
    import signal
    import subprocess

    process = None
    pending = set()

    def forward(signum, _frame):
        if process is None:
            pending.add(signum)
        else:
            process.send_signal(signum)

    previous = {}
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, forward)
        with subprocess.Popen([str(binary), *arguments], cwd=ROOT) as child:
            process = child
            for signum in pending:
                child.send_signal(signum)
            return child.wait()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def runtime(args, binary) -> int:
    """Forward runtime contracts through the invocation's immutable executable."""
    import subprocess

    command = args.command
    if command in ("cli", "perform"):
        forwarded = [*(["perform"] if command == "perform" else []), *args.extra]
        if forwarded[:1] == ["perform"]:
            return perform_forwarded(binary, forwarded)
        return subprocess.call([str(binary), *forwarded], cwd=ROOT)
    if command == "eval":
        return subprocess.run([str(binary), *(["check", "--evaluate"] if args.check else ["run"]), "-"],
                              input=args.source, text=True, cwd=ROOT).returncode
    if command == "exec":
        return subprocess.call([str(binary), "exec", *([args.output_format] if args.output_format else []), "--", args.source], cwd=ROOT)
    if command == "inventory":
        from dataclasses import replace
        from tooling.inventory import inventory
        return inventory(replace(test_context(), binary=binary).helix, ROOT)
    if command in ("run", "recover", "resume", "check"):
        return subprocess.call([str(binary), command, *(["--evaluate"] if command == "check" and args.evaluate else []), args.file], cwd=ROOT)
    if command in ("fmt", "step", "trace", "watch", "state", "repl", "schema", "lsp", "completion"):
        extra = [args.file] if hasattr(args, "file") else []
        for name in ("steps", "limit", "interval_ms", "iterations"):
            value = getattr(args, name, None)
            if value is not None:
                extra.extend(["--" + name.replace("_", "-"), str(value)])
        if command == "completion":
            extra.append(args.shell)
        return subprocess.call([str(binary), "inspect" if command == "state" else command, *extra], cwd=ROOT)
    if command == "sandbox":
        return subprocess.call([str(binary), "sandbox", args.root, args.file], cwd=ROOT)
    if command == "std":
        if args.module is None and args.format == "json":
            result = subprocess.run([str(binary), "std"], cwd=ROOT, capture_output=True, text=True)
            emit({"command": "std", "status": "failed" if result.returncode else "ok",
                  "exit_code": result.returncode, "modules": result.stdout.splitlines(), "stderr": result.stderr})
            return result.returncode
        return subprocess.call([str(binary), "std", *([args.module] if args.module else [])], cwd=ROOT)
    raise AssertionError(f"unhandled runtime command: {command}")


if __name__ == "__main__":
    sys.exit(main())
