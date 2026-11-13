"""Inline and forwarded CLI contracts, reached through manage.py test --suite cli."""
import contextlib
import io
import json
import os
import subprocess
import sys

import yaml

from tooling.cli import SUITES


def verify(h):
    def invoke(*args):
        return subprocess.run(
            [sys.executable, str(h.root / "manage.py"), *args],
            cwd=h.root, input="", capture_output=True, text=True, timeout=120,
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
    help_result = invoke("--help")
    assert help_result.returncode == 0
    assert "eval" in help_result.stdout and "cli" in help_result.stdout

    discovery = invoke("--help-json")
    assert discovery.returncode == 0 and not discovery.stderr
    catalog = json.loads(discovery.stdout)
    assert catalog["version"] == 1
    assert set(catalog["tree"]["commands"]) == {"build", "runtime", "inspect", "quality"}
    assert list(catalog["suites"]) == list(SUITES)
    assert json.loads(invoke("--format=json", "quality", "test", "--list").stdout)["suites"] == list(SUITES)
    assert invoke("--format=text", "test", "--list").stdout.splitlines() == list(SUITES)
    descriptions = invoke("--format=json", "test", "--describe")
    assert descriptions.returncode == 0
    assert json.loads(descriptions.stdout)["suites"] == SUITES
    assert json.loads(invoke("--format=json", "--help").stdout) == catalog
    assert "usage:" in invoke("--format", "text", "--help").stdout

    def walk(node, path=()):
        help_result = invoke("--format=json", *path, "--help")
        assert help_result.returncode == 0 and not help_result.stderr, path
        assert json.loads(help_result.stdout)["tree"] == node
        for name, child in node["commands"].items():
            walk(child, (*path, name))

    walk(catalog["tree"])
    for path in [(), ("runtime",), ("inspect",), ("quality",)]:
        result = invoke("--format=json", *path)
        assert result.returncode == 0 and json.loads(result.stdout)["tree"] and not result.stderr
    # Discovery needs only Python's standard library, with no Cargo or PyYAML.
    bare = subprocess.run(
        [sys.executable, "-S", str(h.root / "manage.py"), "--help-json"],
        cwd=h.root, capture_output=True, text=True, timeout=10,
    )
    assert bare.returncode == 0 and json.loads(bare.stdout) == catalog
    # Invalid fixture selection must fail before trying to build, even without Cargo.
    unmatched = subprocess.run(
        [sys.executable, str(h.root / "manage.py"), "--format=json", "test", "--suite", "golden",
         "--case", "__no_fixture_for_context_contract__"],
        cwd=h.root, env={**os.environ, "PATH": ""}, capture_output=True, text=True, timeout=10,
    )
    assert unmatched.returncode == 2 and not unmatched.stdout and "matched no" in unmatched.stderr
    for args in [("runtime", "unknown"), ("runtime", "run"),
                 ("quality", "test", "--suite", "unknown"),
                 ("quality", "test", "--bless", "--suite", "cli"),
                 ("--help-json", "build")]:
        result = invoke("--format=json", *args)
        assert result.returncode == 2 and not result.stdout, args
        assert json.loads(result.stderr)["error"]["kind"] == "usage", args
    text_error = invoke("--format=text", "runtime", "unknown")
    assert text_error.returncode == 2 and "error:" in text_error.stderr
    assert json.loads(unmatched.stderr)["error"]["kind"] == "selection"
    missing_cargo = subprocess.run(
        [sys.executable, str(h.root / "manage.py"), "--format=json", "build"], cwd=h.root,
        env={**os.environ, "PATH": ""}, capture_output=True, text=True, timeout=10,
    )
    assert missing_cargo.returncode == 2 and not missing_cargo.stderr
    build_error = json.loads(missing_cargo.stdout)
    assert build_error["exit_code"] == 2 and build_error["status"] == "failed"
    for args in [("test", "--suite", "startup"), ("build",), ("std",)]:
        result = invoke("--format=json", *args)
        report = json.loads(result.stdout)
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

    # All shortcuts and canonical paths share parsed arguments, including opaque flags.
    from tooling.cli import parse
    for name, path in catalog["shortcuts"].items():
        extra = {"run": ["-"], "check": ["-"], "recover": ["-"], "resume": ["-"],
                 "fmt": ["-"], "step": ["-"], "trace": ["-"], "watch": ["example.yaml"],
                 "state": ["-"], "completion": ["bash"], "sandbox": ["-", "-"], "eval": ["--", "-42"], "cli": ["--help", "--unknown"],
                 "lint": ["--format=text"], "test": ["--suite", "cli"], "migrate-syntax": ["tests/cases"]}.get(name, [])
        short, full = parse([name, *extra]), parse([*path, *extra])
        assert {k: v for k, v in vars(short).items() if k != "help_parser"} == {
            k: v for k, v in vars(full).items() if k != "help_parser"
        }, name
    assert parse(["cli", "--", "--help"]).extra == ["--help"]
    assert parse(["--format=text", "cli", "--", "--format=json"]).extra == ["--format=json"]
    with contextlib.redirect_stdout(io.StringIO()):
        assert parse(["lint", "--help"]) is None


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
        "SELECT command, exit_code, program, stdin_program FROM calls ORDER BY id").fetchall()
    assert rows == [("eval", 0, '{"$call": ["add", 20, 22]}', 0), ("eval", 1, '{"$call": ["div", 1, 0]}', 0),
                    ("std", 0, None, 0)], rows
    report = json.loads(invoke("--format=json", "usage", "--sql", "SELECT COUNT(*) AS n FROM calls").stdout)
    assert report["query"] == [{"n": 3}]
    assert invoke("usage", "--sql", "DELETE FROM calls").returncode == 2
    unusable = invoke("usage", HELIX_USAGE_DB=str(h.root / "runtime" / "missing" / "usage.sqlite3"))
    assert unusable.returncode == 2 and "usage log unavailable" in unusable.stderr
    database.unlink()
