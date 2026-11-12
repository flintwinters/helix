"""helix exec pipeline contracts, invoked by manage.py test --suite pipeline."""
import json
import os
import shutil
import subprocess
import sys

import yaml


def verify(h):
    verify_formats(h)
    verify_consumers(h)
    for args, source in [(('{"$call": ["add", 20, 22]}',), None), ((), '{"$call": ["add", 20, 22]}'),
                         (("-",), '{"$call": ["add", 20, 22]}')]:
        result = h.helix("exec", *args, stdin=source)
        assert (result.returncode, result.stdout, result.stderr) == (0, "42\n", ""), result
    for source, expected in [('"[add, 20, 22]"', '[add, 20, 22]'),
                             ('""', ""), ('"a\\nb\\n"', "a\nb\n"),
                             ('"雪"', "雪")]:
        result = h.helix("exec", source)
        assert (result.returncode, result.stdout, result.stderr) == (0, expected, ""), result
    for source, expected in [("null", None), ("false", False),
                             ('{"$call": ["list", "add", 1, 2]}', ["add", 1, 2]),
                             ('{"answer": {"$call": ["add", 20, 22]}}', {"answer": 42}),
                             ('{$literal: {$call: [add, 1, 2]}}', {"$call": ["add", 1, 2]})]:
        result = h.helix("exec", source)
        assert result.returncode == 0 and not result.stderr, result
        assert yaml.safe_load(result.stdout) == expected, result
        again = h.helix("exec", stdin=yaml.safe_dump({"$literal": yaml.safe_load(result.stdout)}))
        assert again.returncode == 0 and again.stdout == result.stdout, again
    for source in ('{"$call": ["fn", [], 42]}', '{"$call": ["vm", {"$call": ["fn", [], 42]}]}'):
        for option in ([],["--json"],["--yaml"]):
            result=h.helix("exec",*option,source)
            assert result.returncode==0 and not result.stderr,result
            view=yaml.safe_load(result.stdout)
            assert view["value"]["ref"] in view["objects"],view
    for source in ("[", '{"$call": ["div", 1, 0]}', '{"$call": ["add", {"$ref": "missing"}, 1]}'):
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
        ['bash', '-o', 'pipefail', '-c', '"$1" exec \'"{\\"$call\\": [\\"add\\", 20, 22]}"\' | "$1" exec',
         'helix-pipeline', str(h.binary)],
        capture_output=True, text=True, cwd=h.root, timeout=10,
    )
    assert (result.returncode, result.stdout, result.stderr) == (0, "42\n", ""), result
    # The project entrypoint exposes the same language behavior and discovery.
    for args, source in [(("runtime", "exec", '{"$call": ["add", 20, 22]}'), None),
                         (("exec",), '{"$call": ["add", 20, 22]}'),
                         (("runtime", "exec", "--", "-42"), None)]:
        result = subprocess.run(
            [sys.executable, str(h.root / "manage.py"), *args], input=source,
            capture_output=True, text=True, cwd=h.root, timeout=120,
        )
        expected = "-42\n" if "-42" in args else "42\n"
        assert (result.returncode, result.stdout, result.stderr) == (0, expected, ""), result
    from tooling.cli import parse, parser, describe
    assert parse(["exec"]).source == "-"
    assert parse(["runtime", "exec", "--", "-42"]).source == "-42"
    catalog = describe(parser()[0])
    assert "exec" in catalog["commands"]["runtime"]["commands"]


def verify_formats(h):
    cases = [('"null"', "null"), ('""', ""), ('"true"', "true"),
             ('"a: b"', "a: b"), ('"a\\nb\\t\\u0000雪😀"', "a\nb\t\x00雪😀"),
             ("null", None), ("false", False), ("[]", []), ("{}", {}),
             ("+.5", 0.5), ("1.", 1.0), ("1e+3", 1000.0), ("1e20", 1e20), ("1e-20", 1e-20),
             ("9223372036854775807", 9223372036854775807),
             ('{"answer": {"$call": ["add", 20, 22]}, "nested": {"$call": ["list", "null", false, null]}}',
              {"answer": 42, "nested": ["null", False, None]})]
    for flag, decode in [("--json", json.loads), ("--yaml", yaml.safe_load)]:
        for source, expected in cases:
            result = h.helix("exec", flag, stdin=source)
            assert result.returncode == 0 and not result.stderr, result
            assert decode(result.stdout) == expected, result
        for source in ("[", '{"$call": ["div", 1, 0]}'):
            result = h.helix("exec", flag, source)
            assert result.returncode == 1 and not result.stdout, result
            assert h.malformed_state(result.stderr, 1) is None, result
        assert decode(h.helix("exec", flag, "--", "--literal").stdout) == "--literal"
        result = subprocess.run(
            [sys.executable, str(h.root / "manage.py"), "runtime", "exec", flag],
            input='"null"', capture_output=True, text=True, cwd=h.root, timeout=120,
        )
        assert result.returncode == 0 and not result.stderr and decode(result.stdout) == "null", result
    for args in [("--json", "--yaml"), ("--yaml", "--json"), ("--unknown",),
                 ("--json", "1", "2")]:
        result = h.helix("exec", *args)
        assert result.returncode == 2 and not result.stdout, result
    from tooling.cli import parse
    assert parse(["exec", "--json"]).output_format == "--json"
    assert parse(["exec", "--yaml"]).output_format == "--yaml"


def verify_consumers(h):
    for consumer, formats in [("jq", ["--json"]), ("yq", ["--json", "--yaml"])]:
        executable = shutil.which(consumer)
        if not executable:
            assert not os.environ.get("HELIX_REQUIRE_PIPELINE_TOOLS"), f"missing {consumer}"
            print(f"  SKIP external {consumer} pipes: executable unavailable")
            continue
        for flag in formats:
            # Round trip through a real downstream filter and back into Helix stdin.
            result = subprocess.run(
                ["bash", "-o", "pipefail", "-c",
                 "\"$1\" exec \"$2\" '{answer: {$call: [add, 20, 22]}}' | \"$3\" '.answer' | \"$1\" exec --json",
                 "helix-consumer", str(h.binary), flag, executable],
                capture_output=True, text=True, cwd=h.root, timeout=10,
            )
            assert (result.returncode, result.stdout, result.stderr) == (0, "42\n", ""), result
