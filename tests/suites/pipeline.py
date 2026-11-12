"""hx pipeline contracts, invoked by manage.py test --suite pipeline."""
import subprocess
from unittest.mock import patch

import yaml


def verify(h):
    with patch.object(h, "BIN", h.BIN.with_name("hx")):
        for args, source in [(("[add, 20, 22]",), None), ((), "[add, 20, 22]"),
                             (("-",), "[add, 20, 22]")]:
            result = h.helix(*args, stdin=source)
            assert (result.returncode, result.stdout, result.stderr) == (0, "42\n", ""), result
        for source, expected in [('"[add, 20, 22]"', "[add, 20, 22]"),
                                 ('""', ""), ('"a\\nb\\n"', "a\nb\n"),
                                 ('"雪"', "雪")]:
            result = h.helix(source)
            assert (result.returncode, result.stdout, result.stderr) == (0, expected, ""), result
        for source, expected in [("null", None), ("false", False),
                                 ('[list, "add", 1, 2]', ["add", 1, 2]),
                                 ("{answer: [add, 20, 22]}", {"answer": 42})]:
            result = h.helix(source)
            assert result.returncode == 0 and not result.stderr, result
            assert yaml.safe_load(result.stdout) == expected, result
            again = h.helix(stdin=result.stdout)
            assert again.returncode == 0 and again.stdout == result.stdout, again
        for source in ("[", "[div, 1, 0]", "[add, missing, 1]"):
            result = h.helix(source)
            assert result.returncode == 1 and not result.stdout, result
            assert h.malformed_state(result.stderr, 1) is None, result
        assert h.helix("--help").returncode == 0
        result = h.helix("1", "2")
        assert result.returncode == 2 and not result.stdout and result.stderr, result
        # Exercise the actual OS pipe, including raw generated source and stdin defaults.
        result = subprocess.run(
            ['bash', '-o', 'pipefail', '-c', '"$1" \'"[add, 20, 22]"\' | "$1"',
             'hx-pipeline', str(h.BIN)],
            capture_output=True, text=True, cwd=h.ROOT, timeout=10,
        )
        assert (result.returncode, result.stdout, result.stderr) == (0, "42\n", ""), result
