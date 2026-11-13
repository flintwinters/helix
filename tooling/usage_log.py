"""Usage telemetry: one SQLite row per runtime invocation made through manage.py.

Records what callers (typically coding agents) ask Helix to do, so usage can be
analysed with SQL. It never alters the forwarded process's streams or exit code,
and a logging failure only warns. Project test runs set HELIX_USAGE_LOG=0 so
fixtures do not pollute the log; HELIX_USAGE_DB relocates the database.
"""
import hashlib
import json
import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DISABLE = "HELIX_USAGE_LOG"
LOCATION = "HELIX_USAGE_DB"
PROGRAM_LIMIT = 64 * 1024

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,      -- UTC ISO-8601
    duration_ms INTEGER,           -- NULL when interrupted before the child returned
    command TEXT NOT NULL,         -- manage.py command, e.g. eval, run, exec, cli, perform
    argv TEXT NOT NULL,            -- JSON array: manage.py arguments as typed
    cwd TEXT NOT NULL,
    exit_code INTEGER,             -- NULL when interrupted
    program_path TEXT,             -- source file argument, if any (repository-relative)
    program TEXT,                  -- inline source, or file contents at invocation (first 64 KiB)
    program_sha256 TEXT,
    stdin_program INTEGER NOT NULL,-- 1 when the program was read from stdin (content not captured)
    agent TEXT                     -- HELIX_AGENT, else claude-code when CLAUDECODE is set
)
"""
VERSION = 1


def enabled():
    return os.environ.get(DISABLE) != "0"


def database(root):
    return Path(os.environ.get(LOCATION) or Path(root) / "runtime" / "usage.sqlite3")


def connect(root, *, readonly=False):
    path = database(root)
    if readonly:
        return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    if db.execute("PRAGMA user_version").fetchone()[0] < VERSION:
        db.execute(SCHEMA)
        db.execute(f"PRAGMA user_version = {VERSION}")
    return db


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


@contextmanager
def logged(root, args, argv):
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
    try:
        yield outcome
    finally:
        try:
            exit_code = outcome.get("exit_code")
            with connect(root) as db:
                db.execute(
                    "INSERT INTO calls (started_at, duration_ms, command, argv, cwd, exit_code, program_path,"
                    " program, program_sha256, stdin_program, agent) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (started.isoformat(timespec="milliseconds"),
                     round((time.monotonic() - clock) * 1000) if exit_code is not None else None,
                     args.command, json.dumps(argv), os.getcwd(), exit_code, path,
                     text[:PROGRAM_LIMIT] if text is not None else None,
                     hashlib.sha256(text.encode()).hexdigest() if text is not None else None,
                     int(stdin), _agent()))
        except (sqlite3.Error, OSError) as error:
            print(f"warning: usage log not written: {error}", file=sys.stderr)


def report(root, *, sql=None, schema=False, limit=10):
    """Canned summary, schema, or read-only query, as JSON-ready tables {name: (columns, rows)}."""
    if schema:
        return {"schema": (["ddl"], [(SCHEMA.strip(),)])}
    db = connect(root, readonly=True)

    def table(query, *params):
        cursor = db.execute(query, params)
        return [c[0] for c in cursor.description or []], cursor.fetchall()

    try:
        if sql:
            return {"query": table(sql)}
        return {
            "by_command": table(
                "SELECT command, COUNT(*) AS calls, SUM(exit_code != 0) AS failed,"
                " CAST(AVG(duration_ms) AS INTEGER) AS avg_ms, MAX(started_at) AS last"
                " FROM calls GROUP BY command ORDER BY calls DESC"),
            "recent": table(
                "SELECT id, started_at, command, exit_code,"
                " substr(replace(coalesce(program, program_path, ''), char(10), ' '), 1, 60) AS program"
                " FROM calls ORDER BY id DESC LIMIT ?", limit),
        }
    finally:
        db.close()
