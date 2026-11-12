"""helix exec pipeline contracts, invoked by manage.py test --suite pipeline."""
import subprocess
import sys

import yaml


def verify(h):
    for args, source in [(("[add, 20, 22]",), None), ((), "[add, 20, 22]"),
                         (("-",), "[add, 20, 22]")]:
        result = h.helix("exec", *args, stdin=source)
        assert (result.returncode, result.stdout, result.stderr) == (0, "42\n", ""), result
    for source, expected in [('"[add, 20, 22]"', "[add, 20, 22]"),
                             ('""', ""), ('"a\\nb\\n"', "a\nb\n"),
                             ('"雪"', "雪")]:
        result = h.helix("exec", source)
        assert (result.returncode, result.stdout, result.stderr) == (0, expected, ""), result
    for source, expected in [("null", None), ("false", False),
                             ('[list, "add", 1, 2]', ["add", 1, 2]),
                             ("{answer: [add, 20, 22]}", {"answer": 42})]:
        result = h.helix("exec", source)
        assert result.returncode == 0 and not result.stderr, result
        assert yaml.safe_load(result.stdout) == expected, result
        again = h.helix("exec", stdin=result.stdout)
        assert again.returncode == 0 and again.stdout == result.stdout, again
    for source in ("[", "[div, 1, 0]", "[add, missing, 1]"):
        result = h.helix("exec", source)
        assert result.returncode == 1 and not result.stdout, result
        assert h.malformed_state(result.stderr, 1) is None, result
    assert h.helix("--help").returncode == 0
    help_result = h.helix("exec", "--help")
    assert help_result.returncode == 0 and "helix exec" in help_result.stdout
    assert h.helix("exec", "--", "-42").stdout == "-42\n"
    result = h.helix("exec", "1", "2")
    assert result.returncode == 2 and not result.stdout and result.stderr, result
    # Exercise the actual OS pipe, including raw generated source and stdin defaults.
    result = subprocess.run(
        ['bash', '-o', 'pipefail', '-c', '"$1" exec \'"[add, 20, 22]"\' | "$1" exec',
         'helix-pipeline', str(h.BIN)],
        capture_output=True, text=True, cwd=h.ROOT, timeout=10,
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, "42\n", ""), result
    # The project entrypoint exposes the same language behavior and discovery.
    for args, source in [(("runtime", "exec", "[add, 20, 22]"), None),
                         (("exec",), "[add, 20, 22]"),
                         (("runtime", "exec", "--", "-42"), None)]:
        result = subprocess.run(
            [sys.executable, str(h.ROOT / "manage.py"), *args], input=source,
            capture_output=True, text=True, cwd=h.ROOT, timeout=120,
        )
        expected = "-42\n" if "-42" in args else "42\n"
        assert (result.returncode, result.stdout, result.stderr) == (0, expected, ""), result
    from tooling.cli import parse, parser, describe
    assert parse(["exec"]).source == "-"
    assert parse(["runtime", "exec", "--", "-42"]).source == "-42"
    catalog = describe(parser()[0])
    assert "exec" in catalog["commands"]["runtime"]["commands"]
