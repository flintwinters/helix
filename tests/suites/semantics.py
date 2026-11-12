"""Discoverable actor census, arity boundaries and independently specified results."""
import re
import json
import csv
import io

import yaml

from tests.suites.contracts import checked
from tests.suites.source_contracts import location

# An executable witness per actor. Results here are independent of implementation dispatch.
WITNESSES = {
    "effect": ('{"$call": ["try", {"$call": ["type_time", {"$call": ["effect", "time", null]}]}, {"$call": ["fn", ["e"], {"$call": ["get", {"$ref": "e"}, "kind"]}]}]}', 'purity'),
    "json_parse": ('{"$call": ["json_parse", "{\\"x\\":[1,true]}"]}', {"x": [1, True]}),
    "json_dump": ('{"$call": ["json_dump", {"x": 1}]}', '{"x":1}\n'),
    "csv_parse": ('{"$call": ["csv_parse", "a,b\\nc,d"]}', [["a", "b"], ["c", "d"]]),
    "csv_dump": ('{"$call": ["csv_dump", {"$call": ["list", {"$call": ["list", "a", "b"]}]}]}', 'a,b\r\n'),
    "vm": ('{"$call": ["get", {"$call": ["inspect", {"$call": ["vm", {"$call": ["fn", [], 42]}]}]}, "status"]}', 'ready'),
    "step": ('{"$call": ["get", {"$call": ["inspect", {"$call": ["step", {"$call": ["vm", {"$call": ["fn", [], 42]}]}]}]}, "status"]}', 'running'),
    "inspect": ('{"$call": ["get", {"$call": ["inspect", {"$call": ["vm", {"$call": ["fn", [], 42]}]}]}, "status"]}', 'ready'),
    "add": ('{"$call": ["add", 2, 3]}', 5), "mul": ('{"$call": ["mul", 2, 3]}', 6),
    "sub": ('{"$call": ["sub", 2, 3]}', -1), "div": ('{"$call": ["div", 7, 2]}', 3),
    "mod": ('{"$call": ["mod", 7, 2]}', 1), "neg": ('{"$call": ["neg", -2]}', 2),
    "eq": ('{"$call": ["eq", [1, 2], [1.0, 2]]}', True), "ne": ('{"$call": ["ne", 1, 2]}', True),
    "lt": ('{"$call": ["lt", "a", "雪"]}', True), "le": ('{"$call": ["le", 2, 2.0]}', True),
    "gt": ('{"$call": ["gt", 3, 2]}', True), "ge": ('{"$call": ["ge", 2, 2]}', True),
    "not": ('{"$call": ["not", false]}', True), "and": ('{"$call": ["and", false, {"$call": ["div", 1, 0]}]}', False),
    "or": ('{"$call": ["or", true, {"$call": ["div", 1, 0]}]}', True), "if": ('{"$call": ["if", false, {"$call": ["div", 1, 0]}, 7]}', 7),
    "call": ('{"$call": ["call", {"$call": ["fn", ["x"], {"$call": ["add", {"$ref": "x"}, 1]}]}, 4]}', 5),
    "closure": ('{"$call": ["call", {"$call": ["closure", {"n": 2}, {"$call": ["fn", ["x"], {"$call": ["add", {"$ref": "x"}, {"$ref": "n"}]}]}]}, 3]}', 5),
    "cond": ('{"$call": ["cond", false, 1, true, 2, 3]}', 2),
    "let": ('{"$call": ["let", {"x": 3}, {"$call": ["add", {"$ref": "x"}, 2]}]}', 5),
    "fn": ('{"$call": ["call", {"$call": ["fn", ["x"], {"$ref": "x"}]}, 3]}', 3),
    "map": ('{"$call": ["map", {"$call": ["fn", ["x"], {"$call": ["add", {"$ref": "x"}, 1]}]}, [1, 2]]}', [2, 3]),
    "filter": ('{"$call": ["filter", {"$call": ["fn", ["x"], {"$call": ["gt", {"$ref": "x"}, 1]}]}, [1, 2]]}', [2]),
    "fold": ('{"$call": ["fold", {"$call": ["fn", ["a", "x"], {"$call": ["add", {"$ref": "a"}, {"$ref": "x"}]}]}, 0, [1, 2]]}', 3),
    "each": ('{"$call": ["each", {"$call": ["fn", ["x"], {"$ref": "x"}]}, []]}', None),
    "until": ('{"$call": ["until", {"$call": ["fn", ["x"], {"$call": ["ge", {"$ref": "x"}, 2]}]}, {"$call": ["fn", ["x"], {"$call": ["add", {"$ref": "x"}, 1]}]}, 0]}', 2),
    "len": ('{"$call": ["len", "é雪🙂"]}', 3), "concat": ('{"$call": ["concat", "é", "雪🙂"]}', 'é雪🙂'),
    "slice": ('{"$call": ["slice", "é雪🙂", 1, 3]}', '雪🙂'), "get": ('{"$call": ["get", {}, "missing", null]}', None),
    "set": ('{"$call": ["set", {}, "雪", [1, 2]]}', {'雪': [1, 2]}),
    "append": ('{"$call": ["append", [], {"x": 1}]}', [{'x': 1}]),
    "keys": ('{"$call": ["keys", {"a": 1, "b": 2}]}', ['a', 'b']), "values": ('{"$call": ["values", {"a": 1, "b": 2}]}', [1, 2]),
    "has": ('{"$call": ["has", "é雪🙂", "雪"]}', True), "duplicates": ('{"$call": ["duplicates", [1, 1.0, 2, 2]]}', [1, 2]),
    "str": ('{"$call": ["str", null]}', 'null'), "int": ('{"$call": ["int", "-2"]}', -2),
    "float": ('{"$call": ["float", "0.5"]}', 0.5), "type": ('{"$call": ["type", {}]}', 'map'),
    "list_of": ('{"$call": ["is", [1, 2], {"$call": ["list_of", {"$ref": "integer"}]}]}', True),
    "map_of": ('{"$call": ["is", {"a": 1}, {"$call": ["map_of", {"$ref": "integer"}]}]}', True),
    "record": ('{"$call": ["is", {"a": 1}, {"$call": ["record", "a", {"$ref": "integer"}]}]}', True),
    "optional": ('{"$call": ["is", {}, {"$call": ["record", "a", {"$call": ["optional", {"$ref": "integer"}]}]}]}', True),
    "default": ('{"$call": ["is", {}, {"$call": ["record", "a", {"$call": ["default", {"$ref": "integer"}, 3]}]}]}', True),
    "union": ('{"$call": ["is", "雪", {"$call": ["union", {"$ref": "integer"}, {"$ref": "string"}]}]}', True),
    "enum": ('{"$call": ["is", "雪", {"$call": ["enum", "雪", "a"]}]}', True),
    "literal": ('{"$call": ["is", {"a": [1]}, {"$call": ["literal", {"a": [1]}]}]}', True),
    "refine": ('{"$call": ["is", -1, {"$call": ["refine", {"$ref": "integer"}, {"$call": ["fn", ["n"], {"$call": ["gt", {"$ref": "n"}, 0]}]}]}]}', False),
    "with_field": ('{"$call": ["is", {"a": 1}, {"$call": ["with_field", {"$call": ["record"]}, "a", {"$ref": "integer"}]}]}', True),
    "list": ('{"$call": ["list", "add", [1, 2]]}', ['add', [1, 2]]),
    "dict": ('{"$call": ["dict", "a", 1, "b", 2]}', {'a': 1, 'b': 2}),
    "fields": ('{"$call": ["fields", {"$call": ["record", "a", {"$ref": "integer"}]}]}', {'a': 'integer'}),
    "is": ('{"$call": ["is", null, "null"]}', True), "violations": ('{"$call": ["violations", [1, 2], {"$call": ["list_of", {"$ref": "integer"}]}]}', []),
    "expect": ('{"$call": ["expect", 1, {"$ref": "integer"}]}', 1), "subtype": ('{"$call": ["subtype", {"$ref": "integer"}, {"$ref": "any"}]}', True),
    "secret": ('{"$call": ["get", {"$call": ["secret", "vault:key"]}, "ref"]}', 'vault:key'),
    "class": ('{"$call": ["get", {"$call": ["class", "c", {"$call": ["record"]}, {}]}, "name"]}', 'c'),
    "extend": ('{"$call": ["get", {"$call": ["extend", "c", [], {"$call": ["record"]}, {}]}, "name"]}', 'c'),
    "inherit": ('{"$call": ["get", {"$call": ["inherit", "c", [], {}]}, "values"]}', {}),
    "realize": ('{"$call": ["get", {"$call": ["realize", {"$call": ["class", "c", {"$call": ["record"]}, {}]}]}, "values"]}', {}),
    "type_time": ('{"$call": ["type_time", {"$call": ["list_of", {"$ref": "integer"}]}]}', {"type": "list", "of": "integer"}),
    "open_fields": ('{"$call": ["open_fields", {"$call": ["class", "c", {"$call": ["record", "x", {"$ref": "integer"}]}, {}]}]}', {"x": "integer"}),
    "yaml_parse": ('{"$call": ["yaml_parse", "x: [add, 1, 2]"]}', {"x": ["add", 1, 2]}),
    "yaml_dump": ('{"$call": ["yaml_dump", {"x": 1}]}', "x: 1\n"),
    "constrain": ('{"$call": ["get", {"$call": ["realize", {"$call": ["constrain", {"$call": ["class", "c", {"$call": ["record"]}, {}]}, {"$call": ["fn", ["c"], true]}]}]}, "values"]}', {}),
    "parents": ('{"$call": ["parents", {"$call": ["class", "c", {"$call": ["record"]}, {}]}]}', []),
    "drift": ('{"$call": ["drift", {"$call": ["realize", {"$call": ["class", "c", {"$call": ["record"]}, {}]}]}, {}]}', {'missing': {}, 'changed': {}, 'extra': {}}), "converge": ('{"$call": ["converge", {"$call": ["realize", {"$call": ["class", "c", {"$call": ["record"]}, {}]}]}, {}]}', []),
    "import": ('{"m": {"$call": ["import", "std:math"]}, "r": {"$call": ["m.square", 3]}}', None),
    "assert": ('{"$call": ["assert", true]}', None),
    "raise": ('{"$call": ["try", {"$call": ["raise", "test", "message"]}, {"$call": ["fn", ["e"], {"$call": ["get", {"$ref": "e"}, "kind"]}]}]}', 'test'),
    "try": ('{"$call": ["try", {"$call": ["div", 1, 0]}, {"$call": ["fn", ["e"], {"$call": ["get", {"$ref": "e"}, "kind"]}]}]}', 'divide_by_zero'),
    "finally": ('{"$call": ["finally", 42, {"$call": ["assert", true]}]}', 42),
}


# Operations accepting every YAML value cannot have a wrong-type first operand.
UNRESTRICTED = {"eq", "ne", "type", "literal", "list", "enum", "finally", "yaml_dump", "json_dump", "type_time"}
INVALID = {'effect': '{"$call": ["effect", 1, null]}', 'json_parse': '{"$call": ["json_parse", null]}', 'csv_parse': '{"$call": ["csv_parse", null]}', 'csv_dump': '{"$call": ["csv_dump", null]}', 'vm': '{"$call": ["vm", null]}', 'step': '{"$call": ["step", null]}', 'inspect': '{"$call": ["inspect", null]}', 'add': '{"$call": ["add", null, 1]}', 'mul': '{"$call": ["mul", null, 1]}', 'sub': '{"$call": ["sub", null, 1]}', 'div': '{"$call": ["div", null, 1]}', 'mod': '{"$call": ["mod", null, 1]}', 'neg': '{"$call": ["neg", null]}', 'lt': '{"$call": ["lt", null, 1]}', 'le': '{"$call": ["le", null, 1]}', 'gt': '{"$call": ["gt", null, 1]}', 'ge': '{"$call": ["ge", null, 1]}', 'not': '{"$call": ["not", null]}', 'and': '{"$call": ["and", null, true]}', 'or': '{"$call": ["or", null, false]}', 'if': '{"$call": ["if", null, 1, 2]}', 'call': '{"$call": ["call", null]}', 'closure': '{"$call": ["closure", null, {"$call": ["fn", [], 1]}]}', 'cond': '{"$call": ["cond", null, 1, 2]}', 'let': '{"$call": ["let", null, 1]}', 'fn': '{"$call": ["fn", null, 1]}', 'map': '{"$call": ["map", null, [1]]}', 'filter': '{"$call": ["filter", null, [1]]}', 'fold': '{"$call": ["fold", null, 0, [1]]}', 'each': '{"$call": ["each", null, [1]]}', 'until': '{"$call": ["until", null, {"$call": ["fn", ["x"], {"$ref": "x"}]}, 0]}', 'len': '{"$call": ["len", null]}', 'concat': '{"$call": ["concat", null, null]}', 'slice': '{"$call": ["slice", null, 0, 0]}', 'get': '{"$call": ["get", null, 0]}', 'set': '{"$call": ["set", null, 0, 1]}', 'append': '{"$call": ["append", null, 1]}', 'keys': '{"$call": ["keys", null]}', 'values': '{"$call": ["values", null]}', 'has': '{"$call": ["has", null, 1]}', 'duplicates': '{"$call": ["duplicates", null]}', 'str': '{"$call": ["str", []]}', 'int': '{"$call": ["int", []]}', 'float': '{"$call": ["float", []]}', 'list_of': '{"$call": ["list_of", null]}', 'map_of': '{"$call": ["map_of", null]}', 'record': '{"$call": ["record", null, {"$ref": "integer"}]}', 'optional': '{"$call": ["optional", null]}', 'default': '{"$call": ["default", null, 1]}', 'union': '{"$call": ["union", null]}', 'refine': '{"$call": ["refine", null, {"$call": ["fn", ["x"], true]}]}', 'with_field': '{"$call": ["with_field", null, "x", {"$ref": "integer"}]}', 'dict': '{"$call": ["dict", null, 1]}', 'fields': '{"$call": ["fields", null]}', 'is': '{"$call": ["is", 1, null]}', 'violations': '{"$call": ["violations", 1, null]}', 'expect': '{"$call": ["expect", 1, null]}', 'subtype': '{"$call": ["subtype", null, {"$ref": "any"}]}', 'secret': '{"$call": ["secret", null]}', 'class': '{"$call": ["class", null, {"$call": ["record"]}]}', 'extend': '{"$call": ["extend", null, [], {"$call": ["record"]}, {}]}', 'inherit': '{"$call": ["inherit", null, [], {}]}', 'realize': '{"$call": ["realize", null]}', 'parents': '{"$call": ["parents", null]}', 'drift': '{"$call": ["drift", null, {}]}', 'converge': '{"$call": ["converge", null, {}]}', 'import': '{"$call": ["import", null]}', 'assert': '{"$call": ["assert", null]}', 'raise': '{"$call": ["raise", null]}', 'try': '{"$call": ["try", {"$call": ["div", 1, 0]}, null]}', 'yaml_parse': '{"$call": ["yaml_parse", null]}', 'open_fields': '{"$call": ["open_fields", null]}', 'constrain': '{"$call": ["constrain", null, {"$call": ["fn", ["c"], true]}]}'}


def verify(h):
    declarations = (h.root / "src/builtins.rs").read_text()
    arities = re.findall(r'actor\("([a-z_]+)", (\d+), (None|Some\(\d+\)), Evaluation::[A-Za-z]+', declarations)
    assert len(arities) == len({name for name, _, _ in arities}), "actor registrations must be unique"
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
        result = h.helix("run", "-", stdin=arguments)
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
            source = json.dumps({'$call': [name, *([None] * count)]})
            result = h.helix("run", "-", stdin=source)
            assert h.malformed_state(result.stdout, result.returncode) is None, (name, result)
            error = yaml.safe_load(result.stdout)["error"]
            assert error["kind"] == "arity" and error["path"] == "/", (name, count, error)
            report[name]["wrong_arity"] += 1
    for source, expected in [
        ('{"$call": ["len", ""]}', 0), ('{"$call": ["len", []]}', 0), ('{"$call": ["len", {}]}', 0),
        ('{"$call": ["concat", [], []]}', []), ('{"$call": ["concat", "", ""]}', ''), ('{"$call": ["slice", [], 0, 0]}', []),
        ('{"$call": ["keys", {}]}', []), ('{"$call": ["values", {}]}', []), ('{"$call": ["duplicates", []]}', []),
        ('{"$call": ["map", {"$call": ["fn", ["x"], {"$call": ["div", 1, 0]}]}, []]}', []),
        ('{"$call": ["filter", {"$call": ["fn", ["x"], {"$call": ["div", 1, 0]}]}, []]}', []),
        ('{"$call": ["fold", {"$call": ["fn", ["a", "x"], {"$call": ["div", 1, 0]}]}, 7, []]}', 7),
        ('{"$call": ["add", 9223372036854775806, 1]}', 9223372036854775807),
        ('{"$call": ["sub", -9223372036854775807, 1]}', -9223372036854775808),
        ('{"$call": ["div", -7, 2]}', -3), ('{"$call": ["mod", -7, 2]}', -1), ('{"$call": ["add", 0.5, 0.25]}', 0.75),
    ]:
        assert checked(h, source) == expected, source
    for source in ['{"$call": ["add", 9223372036854775807, 1]}', '{"$call": ["neg", -9223372036854775808]}',
                   '{"$call": ["div", -9223372036854775808, -1]}', '{"$call": ["mul", 1e308, 2.0]}']:
        result = h.helix("run", "-", stdin=source)
        assert yaml.safe_load(result.stdout)["error"]["kind"] == "overflow", (source, result)
    # Independent interchange oracles verify escaping and rows without type inference.
    for value in [None, True, -9223372036854775808, 1.25,
                  {"z": ["add", "雪🙂", "\\n", "\n", "\"", "\u0000"], "a": {}}, []]:
        source = json.dumps(value, ensure_ascii=False)
        assert checked(h, f'{{"$call": ["json_parse", {json.dumps(source, ensure_ascii=False)}]}}') == value
        dumped = checked(h, f'{{"$call": ["json_dump", {{"$call": ["json_parse", {json.dumps(source, ensure_ascii=False)}]}}]}}')
        assert json.loads(dumped) == value
    for rows in [[], [[""]], [["a", "b"], ["ragged"]],
                 [["雪🙂", "a,b", 'a"b', "a\nb", "a\rb", "a\r\nb", ""]]]:
        stream = io.StringIO(newline="")
        csv.writer(stream).writerows(rows)
        encoded = json.dumps(stream.getvalue(), ensure_ascii=False)
        assert checked(h, f'{{"$call": ["csv_parse", {encoded}]}}') == rows
        dumped = checked(h, f'{{"$call": ["csv_dump", {{"$call": ["csv_parse", {encoded}]}}]}}')
        assert list(csv.reader(io.StringIO(dumped, newline=""))) == rows
    # Error blame across strict, lazy and callback forms; unused branches are covered above.
    for source, path, column in [('{"$call": ["add", 1, true]}', '/$call/2', 22), ('{"$call": ["if", 1, 2, 3]}', '/$call/1', 18),
                                 ('{"$call": ["get", [], -1]}', '/$call/2', 23), ('{"$call": ["not", 1]}', '/$call/1', 19)]:
        result = h.helix("run", "-", stdin=source)
        error = yaml.safe_load(result.stdout)["error"]
        assert (error["path"], error["line"], error["column"]) == (path, *location(source, path)), error
    artifact = h.root / "runtime" / "coverage"
    artifact.mkdir(exist_ok=True)
    (artifact / "actors.json").write_text(json.dumps({"schema": 1, "scenarios": report,
        "unrestricted_operands": sorted(UNRESTRICTED),
        "limits": "scenario counts, not instrumented Rust branch percentages"}, indent=2) + "\n")
    print(f"semantics: {len(arities)} actors with result witnesses and all declared arity boundaries")
