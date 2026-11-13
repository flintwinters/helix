"""Subtype implications over numeric equality classes, independently chosen witnesses."""
import json

from tests.suites.contracts import checked


def verify(h):
    def call(actor, *args):
        return {"$call": [actor, *args]}

    def ref(name):
        return {"$ref": name}

    def literal(value):
        return call("literal", {"$literal": value})

    number = call("union", "integer", "float")
    cases = [
        ("integer_literal", literal(1), "integer", 1.0, False),
        ("float_literal", literal(1.0), "float", 1, False),
        ("mixed_enum", call("enum", 1, "ok"), call("union", "integer", "string"), 1.0, False),
        ("fractional_float", literal(1.5), "float", 1.5, True),
        ("unrepresentable_integer", literal(9007199254740993), "integer", 9007199254740993, True),
        ("integer_max", literal(9223372036854775807), "integer", 9223372036854775807, True),
        ("integer_min", literal(-9223372036854775808), "integer", -9223372036854775808.0, False),
        ("number_union", literal(1), number, 1.0, True),
        ("nested_list", literal([1]), call("list_of", "integer"), [1.0], False),
        ("nested_map", literal({"x": 1}), call("map_of", "integer"), {"x": 1.0}, False),
        ("nested_record", literal({"x": [1]}), call("record", "x", call("list_of", "integer")), {"x": [1.0]}, False),
        ("equal_literal", literal([1]), literal([1.0]), [1.0], True),
        ("zero_literal", literal(0), "integer", -0.0, False),
        ("negative_zero_literal", literal(-0.0), "float", 0, False),
    ]
    positive_zero = call("refine", "any", call("fn", ["n"],
        call("ne", call("str", ref("n")), call("str", -0.0))))
    cases.append(("signed_zero_predicate", literal(0.0), positive_zero, -0.0, False))
    # A predicate can distinguish equal numeric representations, including a combination
    # that neither canonical evaluation nor changing only one leaf would reject.
    both_float = call("and", *(call("eq", call("type", call("get", ref("pair"), index)), "float")
                               for index in (0, 1)))
    predicate = call("fn", ["pair"], call("not", both_float))
    cases.append(("correlated_refinement", literal([1, 1]), call("refine", "list", predicate), [1.0, 1.0], False))
    mapping_predicate = call("fn", ["row"], call("not", call("and", *(
        call("eq", call("type", call("get", ref("row"), key)), "float") for key in ("x", "y")))))
    constrained = call("constrain", call("class", "numbers", call("record", "x", number, "y", number)), mapping_predicate)
    cases.append(("correlated_class", literal({"x": 1, "y": 1}), constrained, {"x": 1.0, "y": 1.0}, False))

    # A structural proof must factor through element types rather than enumerate
    # the exponentially many int/float assignments admitted by these literals.
    for size in (32, 128):
        cases.append((f"wide_numeric_list_{size}", literal([1] * size),
                      call("list_of", number), [1.0] * size, True))
    source = {name: call("list", call("is", {"$literal": witness}, candidate),
                         call("is", {"$literal": witness}, target), call("subtype", candidate, target))
              for name, candidate, target, witness, _ in cases}
    results = checked(h, json.dumps(source))
    for name, _, _, _, expected in cases:
        member, target_member, below = results[name]
        assert member, (name, results[name])
        assert target_member is expected, (name, results[name], expected)
        assert not below or target_member, f"unsound subtype: {name}: {results[name]}"
        assert below is expected, (name, results[name], expected)
    print(f"subtype: {len(cases)} numeric equality witnesses, nested data and representation-sensitive predicates")
