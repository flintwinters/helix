#!/usr/bin/env python3
"""Single entrypoint for Helix; use --help or --help-json to discover commands."""
import json
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

    lock_path = ROOT / "runtime" / "build.lock"
    try:  # mkdir of an existing directory is still a network round trip on NFS
        lock = lock_path.open("a")
    except FileNotFoundError:
        lock_path.parent.mkdir(exist_ok=True)
        lock = lock_path.open("a")
    with lock:
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


def _identity(binary) -> str:
    status = os.stat(binary)
    return f"{status.st_dev}-{status.st_ino}-{status.st_mtime_ns}"


def _fresh(binary, stamp) -> bool:
    """Whether every input Cargo recorded for the stamped build predates that build's start.

    The stamp's mtime is set by the project filesystem before Cargo reads any source, so an
    edit racing the build stays newer; a binary replaced by another builder fails the identity.
    """
    import re

    try:
        started = stamp.stat().st_mtime_ns
        if stamp.read_text() != _identity(binary):
            return False
        recorded = Path(binary).with_suffix(".d").read_text().partition(": ")[2].strip()
        inputs = [ROOT / "Cargo.toml", ROOT / "Cargo.lock",
                  *(Path(path.replace("\\ ", " ")) for path in re.split(r"(?<!\\)\s+", recorded) if path)]
        return all(os.stat(path).st_mtime_ns < started for path in inputs)
    except OSError:
        return False


def _build_if_stale(binary, output_format=DEFAULT_FORMAT) -> int:
    """Skip the Cargo no-op (most of a wrapper's fixed latency) when the last build is current."""
    stamp = ROOT / "runtime" / "build.stamp"
    if _fresh(binary, stamp):
        return 0
    stamp.write_text("")
    started = stamp.stat().st_mtime_ns
    code = _build_unlocked(output_format=output_format)
    if code == 0:
        stamp.write_text(_identity(binary))
        os.utime(stamp, ns=(started, started))
    return code


@contextmanager
def frozen_binary(binary, output_format=DEFAULT_FORMAT):
    """Build and snapshot atomically; keep execution independent of later builds.

    Snapshots are keyed by the build output's identity and reused across invocations.
    Every link or unlink changes the inode's ctime, which makes NFS clients discard the
    cached executable, so a per-invocation snapshot re-reads the whole binary. A shared
    lock marks a snapshot in use; only unused snapshots of other builds are pruned.
    """
    import fcntl
    import shutil

    snapshots = ROOT / "runtime" / "execution-binary"
    with build_lock():
        code = _build_if_stale(binary, output_format)
        if code:
            snapshot = use = None
        else:
            directory = snapshots / _identity(binary)
            snapshot = directory / "helix"
            if not snapshot.exists():
                directory.mkdir(parents=True, exist_ok=True)
                # Cargo and linkers replace outputs instead of rewriting them, so a
                # hard link freezes the executable without copying it.
                try:
                    os.link(binary, snapshot)
                except OSError:
                    shutil.copy2(binary, directory / "helix.partial")
                    os.replace(directory / "helix.partial", snapshot)
            use = (directory / "use.lock").open("a+")
            fcntl.flock(use, fcntl.LOCK_SH)
            for stale in snapshots.iterdir():
                if stale == directory:
                    continue
                # New users lock only under the build lock, so an idle snapshot stays idle;
                # close before removal so NFS does not keep the lock file as a silly rename.
                with (stale / "use.lock").open("a+") as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                shutil.rmtree(stale)
    try:
        yield snapshot, code
    finally:
        if use is not None:
            use.close()


@contextmanager
def frozen_test_context(context, output_format=DEFAULT_FORMAT):
    """Retain test configuration while sharing the runtime executable lifecycle."""
    from dataclasses import replace

    with frozen_binary(context.binary, output_format) as (binary, code):
        yield (replace(context, binary=binary) if code == 0 else None), code


def usage(args) -> int:
    """Report on the runtime invocation log; read-only and independent of the build."""
    import sqlite3

    from tooling import usage_log

    try:
        tables = usage_log.report(ROOT, sql=args.sql, schema=args.schema, limit=args.limit)
    except (sqlite3.Error, OSError) as error:
        message = f"usage log unavailable ({usage_log.database(ROOT)}): {error}"
        if args.format == "text":
            print(f"error: {message}", file=sys.stderr)
        else:
            emit({"command": "usage", "status": "error", "exit_code": 2, "error": {"kind": "usage_log", "message": message}},
                 stream=sys.stderr)
        return 2
    if args.format == "json":
        emit({"command": "usage", "status": "ok", **{name: [dict(zip(columns, row)) for row in rows]
                                                      for name, (columns, rows) in tables.items()}})
        return 0
    for name, (columns, rows) in tables.items():
        if name != "query":
            print(f"{name}:")
        if name != "schema":
            print("\t".join(columns))
        for row in rows:
            print("\t".join("" if value is None else str(value) for value in row))
    return 0


def test(bless: bool, suites: list[str] | None = None, jobs: int = DEFAULT_TEST_JOBS,
         patterns: list[str] | None = None, output_format: str = DEFAULT_FORMAT,
         rerun_failed: str | None = None) -> int:
    from tooling.test_runner import discover_cases
    from tooling import test_policy
    from tooling.output import test_report

    # Fixtures invoke wrappers; keep them out of the agent usage log.
    os.environ["HELIX_USAGE_LOG"] = "0"
    try:
        previous = test_policy.load_rerun_report(Path(rerun_failed), SUITES) if rerun_failed is not None else None
        context = test_context()
        cases = discover_cases(context.commands, context.root, patterns) if patterns else None
        case_selections = test_policy.rerun_cases(context, SUITES, previous) if previous is not None else None
    except (ValueError, OSError, UnicodeError) as error:
        if output_format == "text":
            print(f"error: {error}", file=sys.stderr)
        else:
            emit({"command": "test", "status": "error", "exit_code": 2,
                  "error": {"kind": "selection", "message": str(error)}}, stream=sys.stderr)
        return 2
    if previous is not None and previous["failure_count"] == 0:
        # An explicit empty rerun is a bootstrap no-op, never the default all-suite selection.
        return test_report([], output_format=output_format)
    if suites and set(suites) == {"startup"}:
        return context.regression(bless, suites, jobs, output_format=output_format)

    with frozen_test_context(context, output_format) as (frozen, code):
        if code:
            return code
        if previous is not None:
            try:
                suites = test_policy.rerun(frozen, SUITES, previous)
                if not suites:
                    raise test_policy.PolicyError("rerun selection omitted all recorded failures")
            except test_policy.PolicyError as error:
                return test_report([{"suite": "orchestration", "status": "failed", "failures": [str(error)]}],
                                   output_format=output_format)
            return frozen.regression(False, suites, jobs, None, output_format, case_selections=case_selections)
        return frozen.regression(bless, suites, jobs, cases, output_format)


def main() -> int:
    args = parse(sys.argv[1:])
    if args is None:
        return 0
    command = args.command
    if command == "agent-bench":
        from tooling.agent_bench import dispatch
        if args.action in ("list", "models", "report"):
            return dispatch(ROOT, args)
        with frozen_test_context(test_context(), args.format) as (frozen, code):
            return code or dispatch(ROOT, args, frozen)
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
        return test(args.bless, args.suite, args.jobs, args.case, args.format, args.rerun_failed)
    if command == "usage":
        return usage(args)
    with frozen_binary(BIN) as (binary, code):
        if code:
            return code
        from tooling.usage_log import logged

        with logged(ROOT, binary, args, sys.argv[1:]) as outcome:
            outcome["exit_code"] = runtime(args, binary)
        return outcome["exit_code"]


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
    if command == "search":
        return subprocess.call([str(binary), "search", *args.extra], cwd=ROOT)
    if command == "reconcile":
        return subprocess.call([str(binary), command, args.file, args.outcome], cwd=ROOT)
    if command == "eval":
        return subprocess.run([str(binary), *(["check", "--evaluate"] if args.check else ["run"]), "-"],
                              input=args.source, text=True, cwd=ROOT).returncode
    if command == "exec":
        options = []
        if args.stream:
            options.append("--stream")
        if args.input is not None:
            options.extend(["--input", args.input])
        if args.file is not None:
            options.extend(["--file", args.file])
        program = [] if args.file is not None else ["--", args.source]
        return subprocess.call([str(binary), "exec", *([args.output_format] if args.output_format else []), *options, *program], cwd=ROOT)
    if command == "format":
        from dataclasses import replace
        from tooling.source_format import format_files
        return format_files(replace(test_context(), binary=binary), args.paths,
                            write=args.write, output_format=args.format)
    if command == "inventory":
        from dataclasses import replace
        from tooling.inventory import inventory
        return inventory(replace(test_context(), binary=binary).helix, ROOT)
    if command in ("run", "recover", "resume", "check"):
        return subprocess.call([str(binary), command, *(["--evaluate"] if command == "check" and args.evaluate else []), args.file], cwd=ROOT)
    if command in ("fmt", "step", "snapshot", "restore", "system-snapshot", "system-restore", "checkpoint", "target-release", "trace", "watch", "state", "repl", "schema", "lsp"):
        extra = [args.file] if hasattr(args, "file") else []
        for name in ("steps", "limit", "interval_ms", "iterations"):
            value = getattr(args, name, None)
            if value is not None:
                extra.extend(["--" + name.replace("_", "-"), str(value)])
        return subprocess.call([str(binary), "inspect" if command == "state" else command, *extra], cwd=ROOT)
    if command == "sandbox":
        return subprocess.call([str(binary), "sandbox", args.root, args.file], cwd=ROOT)
    if command == "std":
        selected = [*([args.module] if args.module else []), *(["--source"] if args.whole else [])]
        if args.format == "json" and not args.whole:
            result = subprocess.run([str(binary), "std", *selected, "--json"], cwd=ROOT, capture_output=True, text=True)
            emit({"command": "std", "status": "failed" if result.returncode else "ok", "exit_code": result.returncode,
                  "result": json.loads(result.stdout) if result.returncode == 0 else None, "stderr": result.stderr})
            return result.returncode
        return subprocess.call([str(binary), "std", *selected], cwd=ROOT)
    raise AssertionError(f"unhandled runtime command: {command}")


if __name__ == "__main__":
    sys.exit(main())
