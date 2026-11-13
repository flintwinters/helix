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


def verify_fixed_origins(h):
    """Class names are labels; fixed-value membership must not parse their display text."""
    for name in ("ordinary", "default of base", "default of default of base"):
        source = {
            "types": {"$call": ["import", "std:types"]},
            "base": {"$call": ["class", "base", {"$call": ["record", "value", {"$call": ["default", "integer", 7]}]}]},
            "defaulted": {"$call": ["realize", {"$ref": "base"}]},
            "fixed": {"$call": ["extend", name, [{"$ref": "defaulted"}], {"$call": ["record"]}, {"value": 7}]},
            "realized": {"$call": ["realize", {"$ref": "fixed"}]},
            "child": {"$call": ["inherit", "child", [{"$ref": "realized"}], {}]},
            "default_child": {"$call": ["inherit", name, [{"$ref": "base"}], {}]},
            "redeclared": {"$call": ["types.redeclare", {"$ref": "default_child"}, [{"$ref": "base"}]]},
            "default_first": {"$call": ["extend", "joined", [{"$ref": "defaulted"}, {"$ref": "fixed"}], {"$call": ["record"]}, {}]},
            "fixed_first": {"$call": ["extend", "joined", [{"$ref": "fixed"}, {"$ref": "defaulted"}], {"$call": ["record"]}, {}]},
            "observations": {"$call": ["list",
                {"$call": ["is", {"value": 7}, {"$ref": "fixed"}]},
                {"$call": ["is", {"value": 8}, {"$ref": "fixed"}]},
                {"$call": ["is", {}, {"$ref": "fixed"}]},
                {"$call": ["is", {"value": 8}, {"$ref": "realized"}]},
                {"$call": ["is", {"value": 8}, {"$ref": "child"}]},
                {"$call": ["is", {"value": 8}, {"$ref": "defaulted"}]},
                {"$call": ["subtype", {"$ref": "defaulted"}, {"$ref": "fixed"}]},
                {"$call": ["eq", {"$ref": "default_child"}, {"$ref": "redeclared"}]},
                {"$call": ["is", {"value": 8}, {"$ref": "redeclared"}]},
                {"$call": ["is", {"value": 8}, {"$ref": "default_first"}]},
                {"$call": ["is", {"value": 8}, {"$ref": "fixed_first"}]}]},
        }
        root = checked(h, json.dumps(source))
        assert root["observations"] == [True, False, False, False, False, True, False, True, True, False, False], (name, root["observations"])
    # Emitted ordinary-data descriptors retain semantic origins when consumed again.
    for key, expected in (("realized", False), ("defaulted", True)):
        literal = {"$literal": root[key]}
        replay = checked(h, json.dumps({
            "membership": {"$call": ["is", {"value": 8}, literal]},
            "realization": {"$call": ["eq", literal, {"$call": ["realize", literal]}]},
        }))
        assert replay == {"membership": expected, "realization": True}, replay
    # Old display-only origins cannot be disambiguated safely, and malformed kinds
    # must fail through the same parser in membership, realization and redeclaration.
    for kind in ("absent", None, "unknown", False):
        malformed = json.loads(json.dumps(root["fixed"]))
        origin = malformed["provenance"]["value"]
        if kind == "absent":
            origin.pop("value_kind")
        else:
            origin["value_kind"] = kind
        literal = {"$literal": malformed}
        for operation in (["is", {"value": 7}, literal], ["realize", literal],
                          ["types.redeclare", literal, []]):
            source = {"$call": ["let", {"types": {"$call": ["import", "std:types"]}}, {"$call": operation}]}
            result = h.helix("run", "-", stdin=json.dumps(source))
            assert result.returncode == 1 and h.malformed_state(result.stdout, 1) is None, result
            error = yaml.safe_load(result.stdout)["error"]
            assert error["kind"] == "type" and "value_kind" in error["message"] and "rebuild" in error["message"], error


def verify_redeclaration(h):
    """Rebuilding owned declarations retains validation, laziness and value origins."""
    call = lambda actor, *args: {'$call': [actor, *args]}
    ref = lambda name: {'$ref': name}
    capture = call('fn', ['tag'], call('fn', [], call('type', ref('tag'))))
    bindings = {
        'types': call('import', 'std:types'),
        'capture': capture,
        'callback': call('capture', 1.0),
        'base': call('class', 'owner', call('record',
            'required', 'integer', 'optional', call('optional', 'string'),
            'defaulted', call('default', 'integer', 7),
            'guarded', call('refine', 'integer', call('fn', ['value'], call('effect', 'time', None))))),
        'rebuilt': call('types.redeclare', ref('base'), []),
        'realized': call('realize', call('class', 'concrete', call('record',
            'number', call('default', 'integer', 7), 'fixed', 'any',
            'callback', call('default', 'function', ref('callback')),
            'optional', call('optional', 'string')), {'fixed': 1.0})),
        'reconstructed': call('types.redeclare', ref('realized'), []),
        'parent': call('class', 'parent', call('record', 'required', 'integer')),
    }
    observations = {
        'fields_preserved': call('eq', ref('base.fields'), ref('rebuilt.fields')),
        'field_names': call('keys', ref('rebuilt.fields')),
        'optional': ref('rebuilt.fields.optional'),
        'defaulted': ref('rebuilt.fields.defaulted'),
        'abstract': call('not', ref('rebuilt.concrete')),
        'abstract_values': ref('rebuilt.values'),
        'realized': ref('reconstructed.concrete'),
        'provenance': ref('reconstructed.provenance'),
        'number': ref('reconstructed.values.number'),
        'fixed_kind': call('type', ref('reconstructed.values.fixed')),
        'callback_capture': call(ref('reconstructed.values.callback')),
        'default_stays_overridable': call('is', call('dict', 'number', 8, 'fixed', 1.0, 'callback', ref('callback')), ref('reconstructed')),
    }
    malformed_field = {'type': 'optional', 'of': {'type': 'list', 'of': 3}}
    invalid_original = call('set', ref('base'), 'fields', {'required': malformed_field})
    invalid_parent = call('set', ref('parent'), 'fields', {'required': malformed_field})
    invalid_constraints = call('set', ref('parent'), 'constraints', [None])
    for name, original, parents in (
        ('invalid_original', invalid_original, []),
        ('invalid_parent_fields', ref('base'), [invalid_parent]),
        ('invalid_parent_constraints', ref('base'), [invalid_constraints]),
    ):
        observations[name] = call('try', call('types.redeclare', original, parents),
                                  call('fn', ['error'], ref('error.kind')))
    result = checked(h, json.dumps(call('let', bindings, observations)))
    assert result == {
        'fields_preserved': True, 'field_names': ['defaulted', 'guarded', 'optional', 'required'],
        'optional': {'type': 'optional', 'of': 'string'},
        'defaulted': {'type': 'default', 'of': 'integer', 'default': 7},
        'abstract': True, 'abstract_values': {}, 'realized': True,
        'provenance': {
            'number': {'type': 'concrete', 'value': 'default of concrete', 'value_kind': 'default'},
            'callback': {'type': 'concrete', 'value': 'default of concrete', 'value_kind': 'default'},
            'fixed': {'type': 'concrete', 'value': 'concrete', 'value_kind': 'explicit'},
            'optional': {'type': 'concrete'},
        },
        'number': 7, 'fixed_kind': 'float', 'callback_capture': 'float',
        'default_stays_overridable': True,
        'invalid_original': 'type', 'invalid_parent_fields': 'type', 'invalid_parent_constraints': 'type',
    }, result


def verify(h):
    verify_redeclaration(h)
    from tests.suites.class_realization import verify as verify_realization
    verify_realization(h)
    from tests.suites.class_merge_identity import verify as verify_merge_identity
    verify_merge_identity(h)
    from tests.suites.predicate_identity import verify as verify_predicates
    verify_predicates(h)
    from tests.suites.subtype_laws import verify as verify_subtypes
    verify_subtypes(h)
    verify_constraint_descriptors(h)
    verify_fixed_origins(h)
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
    assert root['ab']['provenance'] == {'x': {'type': 'base', 'value': 'base', 'value_kind': 'explicit'},
                                       'y': {'type': 'a', 'value': 'a', 'value_kind': 'explicit'},
                                       'z': {'type': 'b', 'value': 'b', 'value_kind': 'explicit'}}
    assert root['missing']['kind'] == 'abstract'
    assert root['missing']['data']['missing'] == [{'field': 'x', 'required_by': 'needs'},
                                                  {'field': 'y', 'required_by': 'needs'}]


def verify_constraint_descriptors(h):
    """Malformed declarations fail even when ordinary membership would short-circuit."""
    call = lambda actor, *args: {"$call": [actor, *args]}
    literal = lambda value: {"$literal": value}
    ref = lambda name: {"$ref": name}
    base_expression = call("realize", call("class", "checked", call("record", "x", ref("integer")), {"x": 1}))
    base = checked(h, json.dumps(base_expression))
    predicate = call("fn", ["value"], True)

    def operations(descriptor):
        return {
            "subtype": call("subtype", literal(descriptor), "any"),
            "empty_list": call("is", [], {"type": "list", "of": literal(descriptor)}),
            "invalid_field": call("is", {"x": "wrong"}, literal(descriptor)),
            "realize": call("realize", literal(descriptor)),
            "constrain": call("constrain", literal(descriptor), predicate),
            "inherit": call("inherit", "child", [literal(descriptor)], {}),
            "drift": call("drift", literal(descriptor), {}),
        }

    for constraints in (None, 7, {}, [None], ["not a function"], [{}]):
        descriptor = {**base, "constraints": constraints}
        for name, expression in operations(descriptor).items():
            process = h.helix("run", "-", stdin=json.dumps({"probe": expression}))
            assert process.returncode == 1 and h.malformed_state(process.stdout, 1) is None, (name, constraints, process)
            error = yaml.safe_load(process.stdout)["error"]
            assert error["kind"] == "type" and "constraint" in error["message"], (name, constraints, error)
            assert error["path"].startswith("/probe"), error

    # Both omission and an explicit empty list are valid declarations; controls
    # prove rejection is about malformed constraints, not every class descriptor.
    for descriptor in (base, {**base, "constraints": []}):
        result = checked(h, json.dumps(operations(descriptor)))
        assert result["subtype"] is True and result["empty_list"] is True, result
        assert result["invalid_field"] is False and result["realize"]["concrete"] is True, result
        assert result["constrain"]["concrete"] is False and result["inherit"]["concrete"] is True, result
        assert result["drift"] == {"missing": {"x": 1}, "changed": {}, "extra": {}}, result

    for value in (None, False, 3, "not a function", {}, []):
        process = h.helix("run", "-", stdin=json.dumps(call("constrain", literal(base), literal(value))))
        assert process.returncode == 1 and h.malformed_state(process.stdout, 1) is None, (value, process)
        error = yaml.safe_load(process.stdout)["error"]
        assert error["kind"] == "type" and "function" in error["message"], error

    # Valid callable constraints remain lazy during construction and declaration
    # validation. Only a fitting candidate reaches the predicate in the engine.
    source = call("let", {
        "pending": call("constrain", literal(base), call("fn", ["value"], call("raise", "probe", "predicate invoked"))),
    }, {"subtype": call("subtype", ref("pending"), "any"),
        "empty_list": call("is", [], call("list_of", ref("pending"))),
        "invalid_field": call("is", {"x": "wrong"}, ref("pending")),
        "concrete": ref("pending.concrete"),
        "reached": call("violations", {"x": 1}, ref("pending"))})
    result = checked(h, json.dumps(source))
    assert result["subtype"] is True and result["empty_list"] is True, result
    assert result["invalid_field"] is False and result["concrete"] is False, result
    assert len(result["reached"]) == 1 and "predicate invoked" in result["reached"][0]["message"], result
