"""Inline and forwarded CLI contracts, reached through manage.py test --suite cli."""
import subprocess
import sys

import yaml


def verify(h):
    def invoke(*args):
        return subprocess.run(
            [sys.executable, str(h.ROOT / "manage.py"), *args],
            cwd=h.ROOT, capture_output=True, text=True, timeout=120,
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
