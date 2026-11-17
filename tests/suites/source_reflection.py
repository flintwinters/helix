"""Authoritative parser and bounded static evidence contracts."""
import json
import yaml


def verify(h):
    source = '''item: {$call: [fn, [[x, {$ref: integer}]], {$ref: x}, {$ref: integer}]}
quoted: {$literal: {$call: [effect, time, {}]}}
"1": ["雪"]
'''
    result = h.helix("exec", "--json", json.dumps({"$call": ["source_parse", source]}))
    assert result.returncode == 0, result
    reflected = json.loads(result.stdout)
    assert reflected["code"]["1"] == ["雪"], reflected
    assert any(n["reference"]["path"] == ["1", 0] for n in reflected["nodes"]), reflected
    functions = [n for n in reflected["nodes"] if n["kind"] == "function"]
    assert len(functions) == 1, functions
    assert functions[0]["types"]["inferred_result"] == "integer", functions
    assert functions[0]["effects"]["state"] == "none_found", functions
    refs = [n for n in reflected["nodes"] if n["kind"] == "reference" and n["name"] == "x"]
    assert refs[0]["binding"]["kind"] == "parameter", refs
    assert not any(n["kind"] == "call" and n.get("actor") == "effect" for n in reflected["nodes"])
    bad = h.helix("exec", json.dumps({"$call": ["source_parse", "["]}))
    assert bad.returncode == 1 and yaml.safe_load(bad.stderr)["error"]["kind"] == "parse", bad
