"""Discoverable actor census, arity boundaries and independently specified results."""
import re
import json

import yaml

from tests.suites.contracts import checked

# An executable witness per actor. Results here are independent of implementation dispatch.
WITNESSES = {
    "add": ('[add, 2, 3]', 5), "mul": ('[mul, 2, 3]', 6),
    "sub": ('[sub, 2, 3]', -1), "div": ('[div, 7, 2]', 3),
    "mod": ('[mod, 7, 2]', 1), "neg": ('[neg, -2]', 2),
    "eq": ('[eq, [1, 2], [1.0, 2]]', True), "ne": ('[ne, 1, 2]', True),
    "lt": ('[lt, "a", "雪"]', True), "le": ('[le, 2, 2.0]', True),
    "gt": ('[gt, 3, 2]', True), "ge": ('[ge, 2, 2]', True),
    "not": ('[not, false]', True), "and": ('[and, false, [div, 1, 0]]', False),
    "or": ('[or, true, [div, 1, 0]]', True), "if": ('[if, false, [div, 1, 0], 7]', 7),
    "call": ('[call, [fn, [x], [add, x, 1]], 4]', 5),
    "closure": ('[call, [closure, {n: 2}, [fn, [x], [add, x, n]]], 3]', 5),
    "cond": ('[cond, false, 1, true, 2, 3]', 2),
    "let": ('[let, {x: 3}, [add, x, 2]]', 5),
    "fn": ('[call, [fn, [x], x], 3]', 3),
    "map": ('[map, [fn, [x], [add, x, 1]], [1, 2]]', [2, 3]),
    "filter": ('[filter, [fn, [x], [gt, x, 1]], [1, 2]]', [2]),
    "fold": ('[fold, [fn, [a, x], [add, a, x]], 0, [1, 2]]', 3),
    "each": ('[each, [fn, [x], x], []]', None),
    "until": ('[until, [fn, [x], [ge, x, 2]], [fn, [x], [add, x, 1]], 0]', 2),
    "len": ('[len, "é雪🙂"]', 3), "concat": ('[concat, "é", "雪🙂"]', 'é雪🙂'),
    "slice": ('[slice, "é雪🙂", 1, 3]', '雪🙂'), "get": ('[get, {}, "missing", null]', None),
    "set": ('[set, {}, "雪", [1, 2]]', {'雪': [1, 2]}),
    "append": ('[append, [], {x: 1}]', [{'x': 1}]),
    "keys": ('[keys, {a: 1, b: 2}]', ['a', 'b']), "values": ('[values, {a: 1, b: 2}]', [1, 2]),
    "has": ('[has, "é雪🙂", "雪"]', True), "duplicates": ('[duplicates, [1, 1.0, 2, 2]]', [1, 2]),
    "str": ('[str, null]', 'null'), "int": ('[int, "-2"]', -2),
    "float": ('[float, "0.5"]', 0.5), "type": ('[type, {}]', 'map'),
    "list_of": ('[is, [1, 2], [list_of, integer]]', True),
    "map_of": ('[is, {a: 1}, [map_of, integer]]', True),
    "record": ('[is, {a: 1}, [record, "a", integer]]', True),
    "optional": ('[is, {}, [record, "a", [optional, integer]]]', True),
    "default": ('[is, {}, [record, "a", [default, integer, 3]]]', True),
    "union": ('[is, "雪", [union, integer, string]]', True),
    "enum": ('[is, "雪", [enum, "雪", "a"]]', True),
    "literal": ('[is, {a: [1]}, [literal, {a: [1]}]]', True),
    "refine": ('[is, -1, [refine, integer, [fn, [n], [gt, n, 0]]]]', False),
    "with_field": ('[is, {a: 1}, [with_field, [record], "a", integer]]', True),
    "list": ('[list, "add", [1, 2]]', ['add', [1, 2]]),
    "dict": ('[dict, "a", 1, "b", 2]', {'a': 1, 'b': 2}),
    "fields": ('[fields, [record, "a", integer]]', {'a': 'integer'}),
    "is": ('[is, null, "null"]', True), "violations": ('[violations, [1, 2], [list_of, integer]]', []),
    "expect": ('[expect, 1, integer]', 1), "subtype": ('[subtype, integer, any]', True),
    "secret": ('[get, [secret, "vault:key"], "ref"]', 'vault:key'),
    "class": ('[get, [class, "c", [record], {}], "name"]', 'c'),
    "extend": ('[get, [extend, "c", [], [record], {}], "name"]', 'c'),
    "inherit": ('[get, [inherit, "c", [], {}], "values"]', {}),
    "realize": ('[get, [realize, [class, "c", [record], {}]], "values"]', {}),
    "type_time": ('[type_time, [list_of, integer]]', {"type": "list", "of": "integer"}),
    "open_fields": ('[open_fields, [class, "c", [record, "x", integer], {}]]', {"x": "integer"}),
    "yaml_parse": ('[yaml_parse, "x: [add, 1, 2]"]', {"x": ["add", 1, 2]}),
    "yaml_dump": ('[yaml_dump, {x: 1}]', "x: 1\n"),
    "constrain": ('[get, [realize, [constrain, [class, "c", [record], {}], [fn, [c], true]]], "values"]', {}),
    "parents": ('[parents, [class, "c", [record], {}]]', []),
    "drift": ('[drift, [realize, [class, "c", [record], {}]], {}]', {'missing': {}, 'changed': {}, 'extra': {}}), "converge": ('[converge, [realize, [class, "c", [record], {}]], {}]', []),
    "import": ('m: [import, std:math]\nr: [m.square, 3]', None),
    "assert": ('[assert, true]', None),
    "raise": ('[try, [raise, "test", "message"], [fn, [e], [get, e, "kind"]]]', 'test'),
    "try": ('[try, [div, 1, 0], [fn, [e], [get, e, "kind"]]]', 'divide_by_zero'),
    "finally": ('[finally, 42, [assert, true]]', 42),
}


# Operations accepting every YAML value cannot have a wrong-type first operand.
UNRESTRICTED = {"eq", "ne", "type", "literal", "list", "enum", "finally", "yaml_dump", "type_time"}
INVALID = {
    "add": "null, 1", "mul": "null, 1", "sub": "null, 1", "div": "null, 1", "mod": "null, 1",
    "neg": "null", "lt": "null, 1", "le": "null, 1", "gt": "null, 1", "ge": "null, 1",
    "not": "null", "and": "null, true", "or": "null, false", "if": "null, 1, 2",
    "call": "null", "closure": "null, [fn, [], 1]", "cond": "null, 1, 2", "let": "null, 1",
    "fn": "null, 1", "map": "null, [1]", "filter": "null, [1]", "fold": "null, 0, [1]",
    "each": "null, [1]", "until": "null, [fn, [x], x], 0",
    "len": "null", "concat": "null, null", "slice": "null, 0, 0", "get": "null, 0",
    "set": "null, 0, 1", "append": "null, 1", "keys": "null", "values": "null",
    "has": "null, 1", "duplicates": "null", "str": "[]", "int": "[]", "float": "[]",
    "list_of": "null", "map_of": "null", "record": "null, integer", "optional": "null",
    "default": "null, 1", "union": "null", "refine": "null, [fn, [x], true]",
    "with_field": "null, \"x\", integer", "dict": "null, 1", "fields": "null",
    "is": "1, null", "violations": "1, null", "expect": "1, null", "subtype": "null, any",
    "secret": "null", "class": "null, [record]", "extend": "null, [], [record], {}",
    "inherit": "null, [], {}", "realize": "null", "parents": "null", "drift": "null, {}",
    "converge": "null, {}", "import": "null", "assert": "null", "raise": "null",
    "try": "[div, 1, 0], null",
    "yaml_parse": "null", "open_fields": "null", "constrain": "null, [fn, [c], true]",
}


def verify(h):
    declarations = (h.ROOT / "src/builtins.rs").read_text()
    arities = re.findall(r'\("([a-z_]+)", (\d+), (None|Some\(\d+\))\)', declarations)
    assert {name for name, _, _ in arities} == WITNESSES.keys(), "update actor witnesses when adding/removing builtins"
    report = {name: {"result": 0, "wrong_type": 0, "wrong_arity": 0} for name in WITNESSES}
    for name, (source, expected) in WITNESSES.items():
        root = checked(h, source)
        if name == "import":
            assert root["r"] == 9
        else:
            assert root == expected, (name, root, expected)
        report[name]["result"] += 1
    for name, arguments in INVALID.items():
        result = h.helix("run", "-", stdin=f"[{name}, {arguments}]")
        assert h.malformed_state(result.stdout, result.returncode) is None, (name, result)
        error = yaml.safe_load(result.stdout)["error"]
        assert error["kind"] == "type", (name, error)
        report[name]["wrong_type"] += 1
    assert INVALID.keys() | UNRESTRICTED == WITNESSES.keys(), "every actor needs a wrong-type witness or an explicit unrestricted-operand exemption"
    for name, minimum, maximum in arities:
        counts = ([int(minimum) - 1] if int(minimum) else [])
        if maximum != "None":
            counts.append(int(re.search(r'\d+', maximum)[0]) + 1)
        for count in counts:
            source = f'[{name}' + ', null' * count + ']'
            result = h.helix("run", "-", stdin=source)
            assert h.malformed_state(result.stdout, result.returncode) is None, (name, result)
            error = yaml.safe_load(result.stdout)["error"]
            assert error["kind"] == "arity" and error["path"] == "/", (name, count, error)
            report[name]["wrong_arity"] += 1
    for source, expected in [
        ('[len, ""]', 0), ('[len, []]', 0), ('[len, {}]', 0),
        ('[concat, [], []]', []), ('[concat, "", ""]', ''), ('[slice, [], 0, 0]', []),
        ('[keys, {}]', []), ('[values, {}]', []), ('[duplicates, []]', []),
        ('[map, [fn, [x], [div, 1, 0]], []]', []),
        ('[filter, [fn, [x], [div, 1, 0]], []]', []),
        ('[fold, [fn, [a, x], [div, 1, 0]], 7, []]', 7),
        ('[add, 9223372036854775806, 1]', 9223372036854775807),
        ('[sub, -9223372036854775807, 1]', -9223372036854775808),
        ('[div, -7, 2]', -3), ('[mod, -7, 2]', -1), ('[add, 0.5, 0.25]', 0.75),
    ]:
        assert checked(h, source) == expected, source
    for source in ['[add, 9223372036854775807, 1]', '[neg, -9223372036854775808]',
                   '[div, -9223372036854775808, -1]', '[mul, 1e308, 2.0]']:
        result = h.helix("run", "-", stdin=source)
        assert yaml.safe_load(result.stdout)["error"]["kind"] == "overflow", (source, result)
    # Error blame across strict, lazy and callback forms; unused branches are covered above.
    for source, path, column in [('[add, 1, true]', '/2', 10), ('[if, 1, 2, 3]', '/1', 6),
                                 ('[get, [], -1]', '/2', 11), ('[not, 1]', '/1', 7)]:
        result = h.helix("run", "-", stdin=source)
        error = yaml.safe_load(result.stdout)["error"]
        assert (error["path"], error["line"], error["column"]) == (path, 1, column), error
    artifact = h.ROOT / "runtime" / "coverage"
    artifact.mkdir(exist_ok=True)
    (artifact / "actors.json").write_text(json.dumps({"schema": 1, "scenarios": report,
        "unrestricted_operands": sorted(UNRESTRICTED),
        "limits": "scenario counts, not instrumented Rust branch percentages"}, indent=2) + "\n")
    print(f"semantics: {len(arities)} actors with result witnesses and all declared arity boundaries")
