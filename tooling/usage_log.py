"""Usage telemetry: one SQLite row per runtime invocation made through manage.py.

Records what callers (typically coding agents) ask Helix to do, and what came back
(complete program text, stdin programs, stdout and stderr), so usage can be analysed
with SQL. Python observes the invocation; tooling/usage_log.yaml, run
under `perform` with a write grant on the database directory only, owns the table
and the insert. Reporting stays read-only Python so it works without a build.
Output is teed at the file-descriptor level, so the forwarded process sees the same
descriptors, ordering and exit code; terminal streams and interactive commands are not
captured. A logging failure only warns. Project test runs set HELIX_USAGE_LOG=0 so
fixtures do not pollute the log; HELIX_USAGE_DB relocates the database.
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path

import yaml

DISABLE = "HELIX_USAGE_LOG"
LOCATION = "HELIX_USAGE_DB"
# Commands that talk to a peer or terminal interactively; their streams are never interposed.
INTERACTIVE = {"lsp", "repl", "watch"}

PROGRAM = Path(__file__).with_suffix(".yaml")


def enabled():
    return os.environ.get(DISABLE) != "0"


def database(root):
    return Path(os.environ.get(LOCATION) or Path(root) / "runtime" / "usage.sqlite3")


def connect(root):
    """Read-only connection; the logging program creates and writes the database."""
    return sqlite3.connect(f"{database(root).as_uri()}?mode=ro", uri=True)


def table_definition():
    """The table definition, as authored in the logging program."""
    return yaml.safe_load(PROGRAM.read_text())["$call"][1]["schema"].strip()


def _program(root, args):
    """Return (path, text, stdin) for the source a command was asked to run."""
    inline = getattr(args, "source", None)
    if inline is not None and inline != "-":
        return None, inline, False
    candidates = [getattr(args, "file", None), *getattr(args, "extra", [])]
    for candidate in candidates:
        if candidate and candidate != "-" and (Path(root) / candidate).is_file():
            text = (Path(root) / candidate).read_text(errors="replace")
            return candidate, text, False
    uses_stdin = inline == "-" or getattr(args, "file", None) == "-" or "-" in getattr(args, "extra", [])
    return None, None, uses_stdin


def _agent():
    return os.environ.get("HELIX_AGENT") or ("claude-code" if os.environ.get("CLAUDECODE") else None)


def append(root, binary, record):
    """Run the Helix logging program for one record; raise OSError describing a failure."""
    path = database(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [str(binary), "perform", "--allow-stdin", "--allow-read", str(path.parent), "--allow-write", str(path.parent), str(PROGRAM), "--", str(path)],
        input=json.dumps(record, ensure_ascii=False), capture_output=True, text=True, cwd=root, timeout=30)
    if result.returncode:
        try:
            state = yaml.safe_load(result.stderr)
        except yaml.YAMLError:
            state = None
        error = state.get("error") if isinstance(state, dict) else None
        detail = error.get("message") if isinstance(error, dict) else None
        raise OSError(detail or result.stderr.strip() or f"exit {result.returncode}")


def _drain(source, sink, chunks):
    """Copy a pipe to its original descriptor immediately, keeping everything seen."""
    forwarding = True
    while chunk := os.read(source, 65536):
        chunks.append(chunk)
        view = memoryview(chunk)
        while forwarding and view:
            try:
                view = view[os.write(sink, view):]
            except OSError:
                forwarding = False  # reader went away; keep draining so the child never blocks
    os.close(source)


@contextmanager
def _tee(fd, streams):
    """Interpose a pipe on a non-terminal descriptor for the body's duration; terminals stay None."""
    if os.isatty(fd):
        streams[fd] = None
        yield
        return
    chunks = streams[fd] = []
    sys.stdout.flush(), sys.stderr.flush()
    saved = os.dup(fd)
    reader, writer = os.pipe()
    os.dup2(writer, fd)
    os.close(writer)
    thread = threading.Thread(target=_drain, args=(reader, saved, chunks))
    thread.start()
    try:
        yield
    finally:
        sys.stdout.flush(), sys.stderr.flush()
        os.dup2(saved, fd)  # closes the last write end, ending the drain
        thread.join()
        os.close(saved)


@contextmanager
def _replay_stdin(root, text):
    """Replace descriptor 0 by a private file holding the already-read text."""
    with tempfile.TemporaryFile(dir=database(root).parent) as spool:
        spool.write(text)
        spool.flush()
        spool.seek(0)
        saved = os.dup(0)
        os.dup2(spool.fileno(), 0)
        try:
            yield
        finally:
            os.dup2(saved, 0)
            os.close(saved)


def _text(chunks):
    return b"".join(chunks).decode(errors="replace") if chunks is not None else None


@contextmanager
def logged(root, binary, args, argv):
    """Time the wrapped invocation and record it; the body sets outcome['exit_code']."""
    if not enabled():
        yield {}
        return
    outcome = {}
    started, clock = datetime.now(timezone.utc), time.monotonic()
    try:
        path, text, stdin = _program(root, args)
    except OSError:
        path, text, stdin = None, None, False
    streams = {}
    with ExitStack() as stack:
        if args.command not in INTERACTIVE:
            if stdin and not os.isatty(0):
                text = sys.stdin.buffer.read()
                stack.enter_context(_replay_stdin(root, text))
                text = text.decode(errors="replace")
            for fd in (1, 2):
                stack.enter_context(_tee(fd, streams))
        try:
            yield outcome
        finally:
            stack.close()  # restore descriptors before reporting a logging failure
            try:
                exit_code = outcome.get("exit_code")
                append(root, binary, {
                    "started_at": started.isoformat(timespec="milliseconds"),
                    "duration_ms": round((time.monotonic() - clock) * 1000) if exit_code is not None else None,
                    "command": args.command, "argv": json.dumps(argv), "cwd": os.getcwd(), "exit_code": exit_code,
                    "program_path": path, "program": text,
                    "program_sha256": hashlib.sha256(text.encode()).hexdigest() if text is not None else None,
                    "stdin_program": int(stdin),
                    "stdout": _text(streams.get(1)), "stderr": _text(streams.get(2)), "agent": _agent(),
                    "caller_role": os.environ.get("HELIX_CALLER_ROLE", "implementer"),
                    "model": os.environ.get("HELIX_MODEL"),
                    "benchmark_run": None, "benchmark_task": None, "benchmark_trial": None})
            except (OSError, subprocess.SubprocessError, yaml.YAMLError) as error:
                print(f"warning: usage log not written: {error}", file=sys.stderr)


def report(root, *, sql=None, schema=False, limit=10):
    """Canned summary, schema, or read-only query, as JSON-ready tables {name: (columns, rows)}."""
    if schema:
        return {"schema": (["ddl"], [(table_definition(),)])}
    db = connect(root)

    def table(query, *params):
        cursor = db.execute(query, params)
        return [c[0] for c in cursor.description or []], cursor.fetchall()

    try:
        if sql:
            return {"query": table(sql)}
        columns = {row[1] for row in db.execute("PRAGMA table_info(calls)")}
        identity = [name for name in ("caller_role", "model", "benchmark_run", "benchmark_task", "benchmark_trial", "agent") if name in columns]
        return {
            "by_caller": table("SELECT " + ", ".join(identity[:2]) + ", COUNT(*) AS calls FROM calls GROUP BY " + ", ".join(identity[:2])),
            "by_command": table(
                "SELECT command, COUNT(*) AS calls, SUM(exit_code != 0) AS failed,"
                " CAST(AVG(duration_ms) AS INTEGER) AS avg_ms, MAX(started_at) AS last"
                " FROM calls GROUP BY command ORDER BY calls DESC"),
            "recent": table(
                "SELECT id, started_at, command, exit_code," + ", ".join(identity) + ","
                " substr(replace(coalesce(program, program_path, ''), char(10), ' '), 1, 60) AS program"
                " FROM calls ORDER BY id DESC LIMIT ?", limit),
        }
    finally:
        db.close()
