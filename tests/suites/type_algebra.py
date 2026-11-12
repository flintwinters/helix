"""Independent finite membership oracle for the implemented structural fragment."""
import json

import yaml

from tests.suites.contracts import checked


def accepts(value, spec):
    if isinstance(spec, str):
        scalar = {"null": type(None), "boolean": bool, "integer": int, "float": float, "string": str,
                  "list": list, "map": dict}
        return spec == "any" or (spec in scalar and type(value) is scalar[spec])
    kind = spec["type"]
    if kind == "union":
        return any(accepts(value, part) for part in spec["of"])
    if kind == "list":
        return isinstance(value, list) and all(accepts(item, spec["of"]) for item in value)
    if kind == "map":
        return isinstance(value, dict) and all(accepts(item, spec["of"]) for item in value.values())
    if kind == "record":
        if not isinstance(value, dict) or value.keys() - spec["fields"].keys():
            return False
        for key, field in spec["fields"].items():
            optional = isinstance(field, dict) and field.get("type") in ("optional", "default")
            if key not in value:
                if not optional:
                    return False
            elif not accepts(value[key], field["of"] if optional else field):
                return False
        return True
    raise AssertionError(f"oracle does not cover {kind}")


def verify(h):
    from tests.suites.descriptor_policy import verify as verify_descriptors
    verify_descriptors(h)
    from tests.suites.convergence_policy import verify as verify_convergence
    verify_convergence(h)
    primitives = ["any", "never", "null", "boolean", "integer", "float", "string", "list", "map"]
    specs = primitives + [
        {"type": "list", "of": "integer"}, {"type": "map", "of": "string"},
        {"type": "union", "of": ["integer", "string"]},
        {"type": "list", "of": {"type": "union", "of": ["integer", "string"]}},
        {"type": "record", "fields": {"x": "integer", "y": {"type": "optional", "of": "string"}}},
        {"type": "record", "fields": {"x": {"type": "default", "of": "integer", "default": 1}}},
        {"type": "list", "of": {"type": "record", "fields": {"x": "integer"}}},
        {"type": "record", "fields": {"x": {"type": "list", "of": {"type": "union", "of": ["null", "integer"]}}}},
    ]
    values = [None, False, True, -1, 0, 1.0, "é雪🙂", [], [1], [False], [1, "x"], [None, 1],
              {}, {"x": 1}, {"x": 1, "y": "ok"}, {"x": "bad"}, {"x": 1, "extra": 2},
              [{"x": 1}], [{"x": 1}, {"x": "bad"}], {"x": [None, 1]}, {"x": [True]}]
    expressions = []
    expected = []
    for spec in specs:
        for value in values:
            expressions.append(json.dumps({'$call': ['is', value, spec]}, ensure_ascii=False))
            expected.append(accepts(value, spec))
    source = '\n'.join(f'c{i}: {expr}' for i, expr in enumerate(expressions))
    root = checked(h, source)
    for i, want in enumerate(expected):
        assert root[f'c{i}'] is want, (expressions[i], root[f'c{i}'], want)
    # Collect every nested rejection in documented field/index order, including extra fields.
    nested = '{"$call": ["violations", {"x": [1, "bad", false], "extra": 0}, {"$call": ["record", "x", {"$call": ["list_of", {"$ref": "integer"}]}, "required", {"$ref": "string"}]}]}'
    violations = checked(h, nested)
    assert [v['at'] for v in violations] == ['/required', '/x/1', '/x/2', '/extra'], violations
    # Invalid defaults, nested descriptors and predicate failures are errors, not ordinary rejection.
    for source in ['{"$call": ["default", {"$ref": "integer"}, "bad"]}', '{"$call": ["is", 1, {"type": "list"}]}',
                   '{"$call": ["is", 1, {"type": "union", "of": []}]}']:
        result = h.helix('run', '-', stdin=source)
        assert result.returncode == 1 and h.malformed_state(result.stdout, 1) is None, result
        assert yaml.safe_load(result.stdout)['error']['kind'] == 'type'
    # Type declaration validation must not depend on reaching a predicate at
    # runtime: subtyping to any and empty containers still reject malformed types.
    for predicate in (None, 1, "not a function", {}, []):
        malformed = {"type": "refine", "of": "integer", "check": predicate}
        for expression in (["subtype", malformed, "any"],
                           ["is", [], {"type": "list", "of": malformed}],
                           ["violations", "outside base type", malformed]):
            result = h.helix('run', '-', stdin=json.dumps({"$call": expression}))
            assert result.returncode == 1 and h.malformed_state(result.stdout, 1) is None, result
            error = yaml.safe_load(result.stdout)['error']
            assert error['kind'] == 'type' and 'function `check`' in error['message'], error
    rejected = checked(h, '{"$call": ["violations", 1, {"$call": ["refine", {"$ref": "integer"}, {"$call": ["fn", ["x"], false]}]}]}')
    broken = checked(h, '{"$call": ["violations", 1, {"$call": ["refine", {"$ref": "integer"}, {"$call": ["fn", ["x"], 1]}]}]}')
    assert rejected[0]['message'] == 'failed its refinement check'
    assert broken == [{'at': '/', 'message': 'refinement check failed to run: returned integer, expected boolean'}]
    constraint = checked(h, '{"$call": ["try", {"$call": ["realize", {"$call": ["constrain", {"$call": ["class", "checked", {"$call": ["record"]}, {}]}, {"$call": ["fn", ["x"], 1]}]}]}, {"$call": ["fn", ["e"], {"$ref": "e"}]}]}')
    message = 'constraint 0 of `checked` failed to run: returned integer, expected boolean'
    assert constraint['kind'] == 'type' and constraint['message'] == message, constraint
    assert constraint['data']['violations'] == [{'at': '/', 'message': message}], constraint
    print(f'types: {len(specs)} descriptions x {len(values)} values = {len(expected)} independent membership judgments')
    source = '{"base": {"$call": ["class", "base", {"$call": ["record", "x", {"$ref": "integer"}]}, {"x": 1}]}, "a": {"$call": ["extend", "a", {"$call": ["list", {"$ref": "base"}]}, {"$call": ["record", "y", {"$ref": "string"}]}, {"y": "v"}]}, "b": {"$call": ["extend", "b", {"$call": ["list", {"$ref": "base"}]}, {"$call": ["record", "z", {"$ref": "boolean"}]}, {"z": true}]}, "ab": {"$call": ["realize", {"$call": ["extend", "joined", {"$call": ["list", {"$ref": "a"}, {"$ref": "b"}]}, {"$call": ["record"]}, {}]}]}, "ba": {"$call": ["realize", {"$call": ["extend", "joined", {"$call": ["list", {"$ref": "b"}, {"$ref": "a"}]}, {"$call": ["record"]}, {}]}]}, "missing": {"$call": ["try", {"$call": ["realize", {"$call": ["class", "needs", {"$call": ["record", "x", {"$ref": "integer"}, "y", {"$ref": "string"}]}, {}]}]}, {"$call": ["fn", ["e"], {"$ref": "e"}]}]}}'
    root = checked(h, source)
    for field in ('fields', 'values', 'provenance'):
        assert root['ab'][field] == root['ba'][field], (field, root['ab'], root['ba'])
    assert root['ab']['values'] == {'x': 1, 'y': 'v', 'z': True}
    assert root['ab']['provenance'] == {'x': {'type': 'base', 'value': 'base'},
                                       'y': {'type': 'a', 'value': 'a'}, 'z': {'type': 'b', 'value': 'b'}}
    assert root['missing']['kind'] == 'abstract'
    assert root['missing']['data']['missing'] == [{'field': 'x', 'required_by': 'needs'},
                                                  {'field': 'y', 'required_by': 'needs'}]
