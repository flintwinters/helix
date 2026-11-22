"""Inline and forwarded CLI contracts, reached through manage.py test --suite cli."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from tooling.cli import SUITES


def verify(h):
    def invoke(*args, stdin=""):
        return subprocess.run(
            [sys.executable, str(h.root / "manage.py"), *args],
            cwd=h.root, input=stdin, capture_output=True, text=True, timeout=120,
        )

    for source, code in [('{"$call": ["add", 20, 22]}', 0), ('{"$call": ["div", 1, 0]}', 1), ("[", 1)]:
        inline = invoke("eval", source)
        direct = h.helix("run", "-", stdin=source)
        assert inline.returncode == direct.returncode == code
        assert inline.stdout == direct.stdout and not inline.stderr
    assert yaml.safe_load(invoke("eval", '{"$call": ["add", 20, 22]}').stdout)["root"] == 42
    source = '{"a": {"$call": ["add", {"$ref": "missing"}, 1]}, "b": {"$call": ["div", 1, 0]}}'
    checked = invoke("eval", "--check", source)
    direct = h.helix("check", "--evaluate", "-", stdin=source)
    assert checked.returncode == direct.returncode == 1
    assert checked.stdout == direct.stdout
    for args in [("std",), ("std", "math"), ("unknown",)]:
        forwarded = invoke("cli", *args)
        direct = h.helix(*args)
        assert (forwarded.returncode, forwarded.stdout, forwarded.stderr) == (
            direct.returncode, direct.stdout, direct.stderr,
        )
    performed = invoke("runtime", "perform", "--", "--help")
    direct = h.helix("perform", "--help")
    assert (performed.returncode, performed.stdout, performed.stderr) == (direct.returncode, direct.stdout, direct.stderr)
    assert "--command-timeout-ms" in performed.stdout and "--command-progress" in performed.stdout
    verify_usage_log(h)
    verify_failed_suite_reruns(h)
    help_result = invoke("--help")
    assert help_result.returncode == 0
    assert "eval" in help_result.stdout and "cli" in help_result.stdout

    discovery = invoke("--help-yaml")
    assert discovery.returncode == 0 and not discovery.stderr
    catalog = yaml.safe_load(discovery.stdout)
    assert catalog["version"] == 1
    assert set(catalog["tree"]["commands"]) == {"build", "runtime", "inspect", "quality"}
    assert list(catalog["suites"]) == list(SUITES)
    assert yaml.safe_load(invoke("--format=yaml", "quality", "test", "--list").stdout)["suites"] == list(SUITES)
    assert invoke("--format=text", "test", "--list").stdout.splitlines() == list(SUITES)
    descriptions = invoke("--format=yaml", "test", "--describe")
    assert descriptions.returncode == 0
    assert yaml.safe_load(descriptions.stdout)["suites"] == SUITES
    assert yaml.safe_load(invoke("--format=yaml", "--help").stdout) == catalog
    assert "usage:" in invoke("--format", "text", "--help").stdout

    def walk(node, path=()):
        help_result = invoke("--format=yaml", *path, "--help")
        assert help_result.returncode == 0 and not help_result.stderr, path
        assert yaml.safe_load(help_result.stdout)["tree"] == node
        for name, child in node["commands"].items():
            walk(child, (*path, name))

    walk(catalog["tree"])
    for path in [(), ("runtime",), ("inspect",), ("quality",)]:
        result = invoke("--format=yaml", *path)
        assert result.returncode == 0 and yaml.safe_load(result.stdout)["tree"] and not result.stderr
    # Discovery needs only Python's standard library, with no Cargo or PyYAML.
    bare = subprocess.run(
        [sys.executable, "-S", str(h.root / "manage.py"), "--help-yaml"],
        cwd=h.root, capture_output=True, text=True, timeout=10,
    )
    assert bare.returncode == 0 and yaml.safe_load(bare.stdout) == catalog
    # Invalid fixture selection must fail before trying to build, even without Cargo.
    unmatched = subprocess.run(
        [sys.executable, str(h.root / "manage.py"), "--format=yaml", "test", "--suite", "golden",
         "--case", "__no_fixture_for_context_contract__"],
        cwd=h.root, env={**os.environ, "PATH": ""}, capture_output=True, text=True, timeout=10,
    )
    assert unmatched.returncode == 2 and not unmatched.stdout and "matched no" in unmatched.stderr
    for args in [("runtime", "unknown"), ("runtime", "run"),
                 ("quality", "test", "--suite", "unknown"),
                 ("quality", "test", "--bless", "--suite", "cli"),
                 ("--help-yaml", "build")]:
        result = invoke("--format=yaml", *args)
        assert result.returncode == 2 and not result.stdout, args
        assert yaml.safe_load(result.stderr)["error"]["kind"] == "usage", args
    text_error = invoke("--format=text", "runtime", "unknown")
    assert text_error.returncode == 2 and "error:" in text_error.stderr
    assert yaml.safe_load(unmatched.stderr)["error"]["kind"] == "selection"
    missing_cargo = subprocess.run(
        [sys.executable, str(h.root / "manage.py"), "--format=yaml", "build"], cwd=h.root,
        env={**os.environ, "PATH": ""}, capture_output=True, text=True, timeout=10,
    )
    assert missing_cargo.returncode == 2 and not missing_cargo.stderr
    build_error = yaml.safe_load(missing_cargo.stdout)
    assert build_error["exit_code"] == 2 and build_error["status"] == "failed"
    for args in [("test", "--suite", "startup"), ("build",), ("std",)]:
        result = invoke("--format=yaml", *args)
        report = yaml.safe_load(result.stdout)
        assert result.returncode == report["exit_code"] == 0, report
        assert report["version"] == 1
        if args[0] == "test":
            assert report["status"] == "passed" and report["failure_count"] == 0
            assert report["results"][0]["suite"] == "startup"
    text_run = invoke("--format=text", "test", "--suite", "startup")
    assert text_run.returncode == 0 and "startup: passed" in text_run.stdout
    for command in ("run", "check", "recover", "resume"):
        result = invoke("runtime", command, "-")
        direct = h.helix(command, "-", stdin="")
        assert (result.returncode, result.stdout) == (direct.returncode, direct.stdout), (
            command, result, direct
        )

    profiled = invoke("runtime", "profile", "-", "--limit", "0", "--top", "0", stdin="answer: !call [add, 1, 2]")
    report = yaml.safe_load(profiled.stdout)
    assert profiled.returncode == 1 and report["stop"] == "limit" and report["advances"] == 0, (profiled, report)
    assert report["by_time"] == report["by_count"] == []

    # All shortcuts and canonical paths share parsed arguments, including opaque flags.
    from tooling.cli import parse
    for name, path in catalog["shortcuts"].items():
        extra = {"run": ["-"], "check": ["-"], "recover": ["-"], "resume": ["-"],
                 "fmt": ["-"], "format": ["std"], "step": ["-"], "snapshot": ["-"], "restore": ["-"], "system-snapshot": ["-"], "system-restore": ["-"], "checkpoint": ["checkpoint.json"], "target-release": ["target.lock"], "reconcile": ["checkpoint.json", "observed.yaml"], "profile": ["-"], "trace": ["-"], "watch": ["example.yaml"],
                 "state": ["-"], "sandbox": ["-", "-"], "eval": ["--", "-42"], "cli": ["--help", "--unknown"],
                 "lint": ["--format=text"], "test": ["--suite", "cli"], "migrate-syntax": ["tests/cases"],
                 "docker-nginx": ["describe"], "agent-bench": ["list"]}.get(name, [])
        short, full = parse([name, *extra]), parse([*path, *extra])
        assert {k: v for k, v in vars(short).items() if k != "help_parser"} == {
            k: v for k, v in vars(full).items() if k != "help_parser"
        }, name
    assert parse(["cli", "--", "--help"]).extra == ["--help"]
    assert parse(["--format=text", "cli", "--", "--format=yaml"]).extra == ["--format=yaml"]
    with contextlib.redirect_stdout(io.StringIO()):
        assert parse(["lint", "--help"]) is None


def verify_failed_suite_reruns(h):
    from tests.suites.test_reports import report_fixture, suite_fixture

    with tempfile.TemporaryDirectory(dir=h.root / "runtime", prefix="cli-rerun-") as temporary:
        path = Path(temporary) / "previous report.yaml"

        def invoke(*extra, bare=False):
            return subprocess.run(
                [sys.executable, str(h.root / "manage.py"),
                 "--format=yaml", "test", "--rerun-failed", str(path), *extra],
                cwd=h.root, env={**os.environ, **({"PATH": ""} if bare else {})},
                capture_output=True, text=True, timeout=10 if bare else 120,
            )

        previous = report_fixture(suite_fixture("startup"), suite_fixture("static", "passed"),
                                  suite_fixture("docker_nginx", "skipped"))
        path.write_text(yaml.safe_dump(previous))
        original = path.read_bytes()
        process = invoke("--jobs", "1")
        result = yaml.safe_load(process.stdout)
        assert process.returncode == 0 and result["status"] == "passed", (process, result)
        assert [row["suite"] for row in result["results"]] == ["startup"], result
        assert result["results"][0]["status"] == "passed", result
        assert path.read_bytes() == original, "rerun overwrote its historical evidence"

        # Even historical blessing metadata cannot rewrite expectations on a rerun.
        for extra in (("--suite", "startup"), ("--case", "*"), ("--bless",),
                      ("--list",), ("--describe",)):
            process = invoke(*extra, bare=True)
            assert process.returncode == 2 and not process.stdout, (extra, process)
            assert yaml.safe_load(process.stderr)["error"]["kind"] == "usage", process
        for previous in (report_fixture(), report_fixture(suite_fixture("startup", "passed"),
                                                         suite_fixture("docker_nginx", "skipped"))):
            path.write_text(yaml.safe_dump(previous))
            process = invoke(bare=True)
            assert process.returncode == 0 and not process.stderr, process
            assert yaml.safe_load(process.stdout) == report_fixture(), process

        # These inputs must be rejected before Cargo or any selected test can run.
        invalid = ["{", "version: 1\nversion: 1\n", json.dumps({**previous, "version": 2}),
                   json.dumps(report_fixture(suite_fixture("obsolete-suite"))),
                   json.dumps(report_fixture(suite_fixture("startup"), suite_fixture("startup"))),
                   json.dumps({**report_fixture(suite_fixture("startup")), "failure_count": 0}),
                   json.dumps(report_fixture(suite_fixture("docker_nginx"))),
                   json.dumps(report_fixture(suite_fixture("orchestration")))]
        for source in invalid:
            path.write_text(source)
            process = invoke(bare=True)
            assert process.returncode == 2 and not process.stdout, (source, process)
            assert yaml.safe_load(process.stderr)["error"]["kind"] == "selection", process
            assert path.read_text() == source
        path.unlink()
        process = invoke(bare=True)
        assert process.returncode == 2 and yaml.safe_load(process.stderr)["error"]["kind"] == "selection", process


def verify_usage_log(h):
    """Runtime invocations are logged without changing behavior; project tests and opt-outs are not."""
    import sqlite3

    database = h.root / "runtime" / "usage-contract.sqlite3"
    database.unlink(missing_ok=True)
    env = {**os.environ, "HELIX_USAGE_DB": str(database), "HELIX_USAGE_LOG": "1"}

    def invoke(*args, **extra):
        return subprocess.run([sys.executable, str(h.root / "manage.py"), *args], cwd=h.root, input="",
                              capture_output=True, text=True, timeout=120, env={**env, **extra})

    ok, failed = invoke("eval", '{"$call": ["add", 20, 22]}'), invoke("eval", '{"$call": ["div", 1, 0]}')
    assert (ok.returncode, failed.returncode) == (0, 1) and not ok.stderr
    assert invoke("eval", "1", HELIX_USAGE_LOG="0").returncode == 0
    assert invoke("std", "math").returncode == 0
    rows = sqlite3.connect(database).execute(
        "SELECT command, exit_code, program, stdin_program, stdout, stderr FROM calls ORDER BY id").fetchall()
    assert [row[:4] for row in rows] == [("eval", 0, '{"$call": ["add", 20, 22]}', 0),
                                         ("eval", 1, '{"$call": ["div", 1, 0]}', 0), ("std", 0, None, 0)], rows
    identity = sqlite3.connect(database).execute("SELECT caller_role, model FROM calls ORDER BY id").fetchall()
    assert identity == [('implementer', None)] * 3, identity
    # Output is stored completely and identically to what the caller received.
    assert [row[4:] for row in rows[:2]] == [(ok.stdout, ""), (failed.stdout, failed.stderr)], rows
    assert rows[2][4] == invoke("std", "math").stdout, rows
    # A program read from stdin is captured, and the child still receives it.
    piped = subprocess.run([sys.executable, str(h.root / "manage.py"), "run", "-"], cwd=h.root, input="[1, 2]\n",
                           capture_output=True, text=True, timeout=120, env=env)
    last = sqlite3.connect(database).execute(
        "SELECT program, stdin_program, stdout FROM calls ORDER BY id DESC LIMIT 1").fetchone()
    assert piped.returncode == 0 and last == ("[1, 2]\n", 1, piped.stdout), (piped, last)
    report = yaml.safe_load(invoke("--format=yaml", "usage", "--sql", "SELECT COUNT(*) AS n FROM calls").stdout)
    assert report["query"] == [{"n": 5}]
    assert invoke("usage", "--sql", "DELETE FROM calls").returncode == 2
    unusable = invoke("usage", HELIX_USAGE_DB=str(h.root / "runtime" / "missing" / "usage.sqlite3"))
    assert unusable.returncode == 2 and "usage log unavailable" in unusable.stderr
    database.unlink()
    # A database created before output capture gains the columns without losing rows.
    legacy = sqlite3.connect(database)
    legacy.execute("CREATE TABLE calls (id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, duration_ms INTEGER,"
                   " command TEXT NOT NULL, argv TEXT NOT NULL, cwd TEXT NOT NULL, exit_code INTEGER,"
                   " program_path TEXT, program TEXT, program_sha256 TEXT, stdin_program INTEGER NOT NULL, agent TEXT)")
    legacy.execute("INSERT INTO calls (started_at, command, argv, cwd, stdin_program) VALUES ('t', 'old', '[]', '.', 0)")
    legacy.commit(), legacy.close()
    assert invoke("eval", "1").returncode == 0
    assert sqlite3.connect(database).execute("SELECT command, stdout FROM calls ORDER BY id").fetchall() == [
        ("old", None), ("eval", "root: 1\nstatus: finished\n")]
    database.unlink()
