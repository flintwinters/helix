"""Subtype proofs must not equate predicates merely because language equality does."""
import json

from tests.suites.contracts import checked


def verify(h):
    def call(actor, *args):
        return {"$call": [actor, *args]}

    def ref(name):
        return {"$ref": name}

    def fn(params, body):
        return call("fn", params, body)

    def outcome(expression):
        return call("try", call("get", expression, "concrete"), fn(["error"], ref("error.kind")))

    bindings = {
        "types": call("import", "std:types"),
        "predicate": fn(["tag"], fn(["value"], call("eq", call("type", ref("value")), call("type", ref("tag"))))),
        "integer_predicate": call("predicate", 1),
        "float_predicate": call("predicate", 1.0),
        "integer_type": call("refine", "any", ref("integer_predicate")),
        "float_type": call("refine", "any", ref("float_predicate")),
        "constraint": fn(["tag"], fn(["record"], call("eq", call("type", call("get", ref("record"), "x")), call("type", ref("tag"))))),
        "integer_constraint": call("constraint", 1),
        "float_constraint": call("constraint", 1.0),
        "base": call("class", "base", call("record", "x", "any"), {"x": 1}),
        "integer_class": call("constrain", ref("base"), ref("integer_constraint")),
        "float_class": call("constrain", ref("base"), ref("float_constraint")),
        "both": call("extend", "both", [ref("integer_class"), ref("float_class")], call("record"), {}),
        "reverse": call("extend", "reverse", [ref("float_class"), ref("integer_class")], call("record"), {}),
        "child": call("constrain", call("extend", "child", [ref("integer_class")], call("record"), {}), ref("float_constraint")),
        "rebuilt": call("types.redeclare", ref("child"), [ref("integer_class")]),
    }
    observations = {
        "language_predicate_equality": call("eq", ref("integer_predicate"), ref("float_predicate")),
        "integer_member": call("is", 1, ref("integer_type")),
        "float_member": call("is", 1, ref("float_type")),
        "refinement_subtype": call("subtype", ref("integer_type"), ref("float_type")),
        "reverse_refinement_subtype": call("subtype", ref("float_type"), ref("integer_type")),
        "class_subtype": call("subtype", ref("integer_class"), ref("float_class")),
        "reverse_class_subtype": call("subtype", ref("float_class"), ref("integer_class")),
        "both_constraints": outcome(call("realize", ref("both"))),
        "reverse_constraints": outcome(call("realize", ref("reverse"))),
        "child_rejects": call("not", call("is", {"x": 1}, ref("child"))),
        "rebuilt_rejects": call("not", call("is", {"x": 1}, ref("rebuilt"))),
        "rebuild_preserves_descriptor": call("eq", ref("child"), ref("rebuilt")),
    }
    expected = {
        "language_predicate_equality": True, "integer_member": True, "float_member": False,
        "refinement_subtype": False, "reverse_refinement_subtype": False,
        "class_subtype": False, "reverse_class_subtype": False,
        "both_constraints": "type", "reverse_constraints": "type",
        "child_rejects": True, "rebuilt_rejects": True, "rebuild_preserves_descriptor": True,
    }
    bindings.update({
        "repeated_constraint": call("constraint", 1),
        "repeated_class": call("constrain", ref("base"), ref("repeated_constraint")),
        "same_parents": call("extend", "same", [ref("integer_class"), ref("repeated_class")], call("record"), {}),
        "empty_constraints": call("set", ref("base"), "constraints", []),
        "realized": call("realize", ref("integer_class")),
    })
    observations.update({
        "same_capture_subtype": call("subtype", ref("integer_class"), ref("repeated_class")),
        "same_capture_deduplicates": call("eq", call("len", ref("same_parents.constraints")), 1),
        "same_capture_realizes": outcome(call("realize", ref("same_parents"))),
        "omitted_constraints_preserved": call("eq", ref("base"), call("types.redeclare", ref("base"), [])),
        "empty_constraints_preserved": call("eq", ref("empty_constraints"), call("types.redeclare", ref("empty_constraints"), [])),
        "realization_preserved": call("eq", ref("realized"), call("types.redeclare", ref("realized"), [])),
        "omitted_rejects_new_parent": outcome(call("types.redeclare", ref("base"), [ref("float_class")])),
        "empty_rejects_new_parent": outcome(call("types.redeclare", ref("empty_constraints"), [ref("float_class")])),
        "present_rejects_new_parent": outcome(call("types.redeclare", ref("integer_class"), [ref("float_class")])),
    })
    expected.update({
        "same_capture_subtype": True, "same_capture_deduplicates": True, "same_capture_realizes": True,
        "omitted_constraints_preserved": True, "empty_constraints_preserved": True, "realization_preserved": True,
        "omitted_rejects_new_parent": "type", "empty_rejects_new_parent": "type", "present_rejects_new_parent": "type",
    })
    # Field declaration merging must also retain predicates inside structural wrappers.
    for label, wrap in (("direct", lambda ty: ty), ("list", lambda ty: call("list_of", ty)),
                        ("optional", lambda ty: call("optional", ty))):
        left = call("class", "left", call("record", "item", wrap(ref("integer_type"))))
        right = call("class", "right", call("record", "item", wrap(ref("float_type"))))
        observations[f"{label}_field_conflict"] = outcome(call("extend", "joined", [left, right], call("record"), {}))
        expected[f"{label}_field_conflict"] = "conflict"
    bindings.update({
        "nested_predicate": fn(["tag"], fn(["value"], call("eq", call("type", ref("value")), call("type", ref("tag.n"))))),
        "nested_left": call("nested_predicate", {"n": 1}),
        "nested_right": call("nested_predicate", {"n": 1.0}),
        "zero_predicate": fn(["tag"], fn(["value"], call("eq", call("json_dump", ref("value")), call("json_dump", ref("tag"))))),
        "zero_left": call("zero_predicate", 0.0),
        "zero_right": call("zero_predicate", -0.0),
    })
    for label, value in (("nested", 1), ("zero", 0.0)):
        left, right = ref(f"{label}_left"), ref(f"{label}_right")
        observations.update({
            f"{label}_language_equal": call("eq", left, right),
            f"{label}_left_accepts": call("is", value, call("refine", "any", left)),
            f"{label}_right_rejects": call("not", call("is", value, call("refine", "any", right))),
            f"{label}_not_subtype": call("not", call("subtype", call("refine", "any", left), call("refine", "any", right))),
        })
        expected.update({f"{label}_{suffix}": True for suffix in
                         ("language_equal", "left_accepts", "right_rejects", "not_subtype")})
    actual = checked(h, json.dumps(call("let", bindings, observations)))
    assert actual == expected, {key: {"actual": actual.get(key), "expected": value}
                                for key, value in expected.items() if actual.get(key) != value}
