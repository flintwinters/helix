"""YAML representation changes preserve explicit Helix program meaning."""
import json
from pathlib import Path
import tempfile

import yaml


def representations(value):
    yield json.dumps(value, ensure_ascii=False)
    yield yaml.safe_dump(value, default_flow_style=True, sort_keys=False)
    yield yaml.safe_dump(value, default_flow_style=False, sort_keys=False)
    yield yaml.safe_dump(value, default_style='"', sort_keys=True)
    def reorder(item):
        if isinstance(item, dict):
            return {key: reorder(value) for key, value in reversed(list(item.items()))}
        if isinstance(item, list):
            return [reorder(value) for value in item]
        return item
    yield yaml.safe_dump(reorder(value), sort_keys=False)


def verify(h):
    verify_migration()
    for source, expected in [
        ('!!str 42', '42'), ('!!int "0x2a"', 42), ('0x2a', 42), ('0o52', 42),
        ('!!float "42"', 42.0), ('!!bool true', True), ('!!null null', None),
        ('!!seq [add, 1, 2]', ['add', 1, 2]), ('!!map {value: 42}', {'value': 42}),
        ('a: &value [1, 2]\nb: *value', {'a': [1, 2], 'b': [1, 2]}),
        ('x: 40\na: &expr {$call: [add, {$ref: x}, 2]}\nb: *expr', {'x': 40, 'a': 42, 'b': 42}),
        ('a: {x: 1, value: &expr {$ref: x}}\nb: {x: 2, value: *expr}',
         {'a': {'x': 1, 'value': 1}, 'b': {'x': 2, 'value': 2}}),
    ]:
        result = h.helix('run', '-', stdin=source)
        state = yaml.safe_load(result.stdout)
        assert result.returncode == 0 and state['root'] == expected, (source, result)
        assert type(state['root']) is type(expected), (source, state)
    for source in ('&loop [*loop]', '&loop {self: *loop}', '&key a: 1\n*key: 2',
                   '1: value', '.nan', '.inf', '9223372036854775808', '!unknown value',
                   '---\n1\n---\n2'):
        result = h.helix('run', '-', stdin=source)
        assert result.returncode == 1 and h.malformed_state(result.stdout, 1) is None, (source, result)
    call = lambda actor, *args: {"$call": [actor, *args]}
    ref = lambda name: {"$ref": name}
    literal = lambda value: {"$literal": value}
    programs = [
        ("add", "add"),
        (["add", 20, 22], ["add", 20, 22]),
        ({"answer": call("add", 20, 22), "text": "answer"}, {"answer": 42, "text": "answer"}),
        ({"answer": call("add", ref("x"), 2), "x": 40}, {"answer": 42, "x": 40}),
        (call("let", {"x": 40}, call("add", ref("x"), 2)), 42),
        (call("let", {"f": call("fn", ["x"], call("add", ref("x"), 1))}, call("f", 41)), 42),
        (literal({"$call": ["raise", "data"]}), {"$call": ["raise", "data"]}),
        (literal({"$ref": "missing"}), {"$ref": "missing"}),
        (literal({"$literal": {"$call": []}}), {"$literal": {"$call": []}}),
        ({"$call": ["add", 1, 2], "other": "ordinary"}, {"$call": ["add", 1, 2], "other": "ordinary"}),
        (call("eq", call("fn", ["x"], call("add", ref("x"), 1)),
              call("fn", ["x"], call("add", ref("x"), 1))), True),
        (call("eq", call("fn", [], 1), call("fn", [], 1.0)), False),
    ]
    for program, expected in programs:
        for source in representations(program):
            result = h.helix("run", "-", stdin=source)
            state = yaml.safe_load(result.stdout)
            assert result.returncode == 0 and state["root"] == expected, (source, result)
            restored = h.root_section(result.stdout)
            assert restored is not None, state
            again = h.helix("run", "-", stdin=json.dumps({"$literal": yaml.safe_load(restored)}, ensure_ascii=False))
            assert again.returncode == 0 and yaml.safe_load(again.stdout)["root"] == expected, again

    function = call("fn", ["x"], call("add", ref("x"), 1))
    first = json.dumps(function)
    for second in representations(function):
        source = '$call:\n- eq\n- ' + first + '\n- ' + second.strip().replace('\n', '\n  ') + '\n'
        result = h.helix('run', '-', stdin=source)
        assert result.returncode == 0 and yaml.safe_load(result.stdout)['root'] is True, (source, result)

    for invalid in ({"$call": []}, {"$call": "add"}, {"$call": None},
                    {"$ref": ""}, {"$ref": 1}, {"$ref": []}):
        kinds = set()
        for source in representations(invalid):
            result = h.helix("run", "-", stdin=source)
            assert result.returncode == 1 and h.malformed_state(result.stdout, 1) is None, result
            kinds.add(yaml.safe_load(result.stdout)["error"]["kind"])
        assert len(kinds) == 1, (invalid, kinds)
    for invalid in (call(42, 1), call(literal("add"), 1, 2),
                    call("let", {"f": 42}, call("f", 1))):
        result = h.helix('run', '-', stdin=json.dumps(invalid))
        assert result.returncode == 1 and yaml.safe_load(result.stdout)['error']['kind'] == 'type', result

    # Effects expose evaluation order independently of dictionary insertion order.
    program = {"z": call("effect", "stdout", "Z"), "a": call("effect", "stdout", "A")}
    with tempfile.TemporaryDirectory(dir=h.root / "runtime", prefix="yaml-model-") as directory:
        path = Path(directory) / "program.yaml"
        for source in representations(program):
            path.write_text(source)
            result = h.helix("perform", "--allow-stdout", str(path))
            state = yaml.safe_load(result.stderr)
            assert result.returncode == 0 and result.stdout == "AZ" and state["status"] == "finished", result


def verify_migration():
    """Offline conversion is explicit, repeatable, and respects old lexical contexts."""
    from tooling.syntax_migration import migrate_source
    cases = [
        ('[add, x, "x"]', {"$call": ["add", {"$ref": "x"}, "x"]}),
        ('[fn, [x], [add, x, 1]]', {"$call": ["fn", ["x"], {"$call": ["add", {"$ref": "x"}, 1]}]}),
        ('[[fn, [x], x], 1]', {"$call": [{"$call": ["fn", ["x"], {"$ref": "x"}]}, 1]}),
        ('["add", 1, 2]', ["add", 1, 2]),
        ('x: unbound', {"x": "unbound"}),
        ('[list, 1e308, "1e20", TRUE]', {"$call": ["list", 1e308, "1e20", True]}),
        ('[list, "a\\x85b"]', {"$call": ["list", "a\x85b"]}),
        ('[let, {f: [fn, [x], x]}, [f, 42]]',
         {"$call": ["let", {"f": {"$call": ["fn", ["x"], {"$ref": "x"}]}}, {"$call": ["f", 42]}]}),
    ]
    for source, expected in cases:
        converted = migrate_source(source)
        assert yaml.safe_load(converted) == expected, converted
        assert migrate_source(converted) == converted
    for source in ('a: 1\na: 2', 'a: &value 1\nb: *value', '&cycle [*cycle]'):
        try:
            migrate_source(source)
        except (ValueError, yaml.YAMLError):
            pass
        else:
            raise AssertionError(f"ambiguous legacy input was rewritten: {source}")
