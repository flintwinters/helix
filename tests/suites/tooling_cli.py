"""Inline and forwarded CLI contracts, reached through manage.py test --suite cli."""
import contextlib
import io
import json
import subprocess
import sys

import yaml


def verify(h):
    def invoke(*args):
        return subprocess.run(
            [sys.executable, str(h.ROOT / "manage.py"), *args],
            cwd=h.ROOT, input="", capture_output=True, text=True, timeout=120,
        )

    for source, code in [("[add, 20, 22]", 0), ("[div, 1, 0]", 1), ("[", 1)]:
        inline = invoke("eval", source)
        direct = h.helix("run", "-", stdin=source)
        assert inline.returncode == direct.returncode == code
        assert inline.stdout == direct.stdout and not inline.stderr
    assert yaml.safe_load(invoke("eval", "[add, 20, 22]").stdout)["root"] == 42
    source = "a: [add, missing, 1]\nb: [div, 1, 0]"
    checked = invoke("eval", "--check", source)
    direct = h.helix("check", "-", stdin=source)
    assert checked.returncode == direct.returncode == 1
    assert checked.stdout == direct.stdout
    for args in [("std",), ("std", "math"), ("unknown",)]:
        forwarded = invoke("cli", *args)
        direct = h.helix(*args)
        assert (forwarded.returncode, forwarded.stdout, forwarded.stderr) == (
            direct.returncode, direct.stdout, direct.stderr,
        )
    help_result = invoke("--help")
    assert help_result.returncode == 0
    assert "eval" in help_result.stdout and "cli" in help_result.stdout

    discovery = invoke("--help-json")
    assert discovery.returncode == 0 and not discovery.stderr
    catalog = json.loads(discovery.stdout)
    assert catalog["version"] == 1
    assert set(catalog["tree"]["commands"]) == {"build", "runtime", "inspect", "quality"}
    assert list(catalog["suites"]) == list(h.SUITES)
    assert invoke("quality", "test", "--list").stdout.splitlines() == list(h.SUITES)
    descriptions = invoke("test", "--describe")
    assert descriptions.returncode == 0
    assert all(description in descriptions.stdout for description in h.SUITES.values())

    def walk(node, path=()):
        help_result = invoke(*path, "--help")
        assert help_result.returncode == 0 and not help_result.stderr, path
        for name, child in node["commands"].items():
            walk(child, (*path, name))

    walk(catalog["tree"])
    for path in [(), ("runtime",), ("inspect",), ("quality",)]:
        result = invoke(*path)
        assert result.returncode == 0 and "usage:" in result.stdout and not result.stderr
    # Discovery needs only Python's standard library, with no Cargo or PyYAML.
    bare = subprocess.run(
        [sys.executable, "-S", str(h.ROOT / "manage.py"), "--help-json"],
        cwd=h.ROOT, capture_output=True, text=True, timeout=10,
    )
    assert bare.returncode == 0 and json.loads(bare.stdout) == catalog
    for args in [("runtime", "unknown"), ("runtime", "run"),
                 ("quality", "test", "--suite", "unknown"),
                 ("quality", "test", "--bless", "--suite", "cli"),
                 ("--help-json", "build")]:
        result = invoke(*args)
        assert result.returncode == 2 and not result.stdout and "error:" in result.stderr, args
    for command in ("run", "check", "recover", "resume"):
        result = invoke("runtime", command, "-")
        direct = h.helix(command, "-", stdin="")
        assert (result.returncode, result.stdout) == (direct.returncode, direct.stdout)

    # All shortcuts and canonical paths share parsed arguments, including opaque flags.
    from tooling.cli import parse
    for name, path in catalog["shortcuts"].items():
        extra = {"run": ["-"], "check": ["-"], "recover": ["-"], "resume": ["-"],
                 "sandbox": ["-", "-"], "eval": ["--", "-42"], "cli": ["--help", "--unknown"],
                 "lint": ["--format=text"], "test": ["--suite", "cli"]}.get(name, [])
        short, full = parse([name, *extra]), parse([*path, *extra])
        assert {k: v for k, v in vars(short).items() if k != "help_parser"} == {
            k: v for k, v in vars(full).items() if k != "help_parser"
        }, name
    assert parse(["cli", "--", "--help"]).extra == ["--help"]
    with contextlib.redirect_stdout(io.StringIO()):
        assert parse(["lint", "--help"]) is None
