"""Equal inherited data must not select observably different representatives by order.

Fixed membership continues to use language equality; merging chooses executable data, which predicates
can distinguish. Ambiguous parent contributions require an explicit child resolution.
"""
import json

from tests.suites.contracts import checked


def verify(h):
    def call(actor, *args):
        return {"$call": [actor, *args]}

    def ref(name):
        return {"$ref": name}

    def outcome(expression):
        return call("try", call("get", call("realize", expression), "concrete"),
                    call("fn", ["error"], ref("error.kind")))

    bindings = {}
    observations = {}
    expected = {}
    # Nested data and signed zero are observable too; top-level numeric tagging
    # alone cannot establish that a representative is interchangeable.
    for label, left, right in (("numeric", 1, 1.0), ("nested", {"n": 1}, {"n": 1.0}),
                               ("zero", 0.0, -0.0)):
        predicate = call("fn", ["record"], call("eq", call("json_dump", ref("record.x")),
                                                call("json_dump", left)))
        schema = call("record", "x", "any")
        bindings[f"{label}_left"] = call("constrain", call("class", "left", schema, {"x": left}), predicate)
        bindings[f"{label}_right"] = call("class", "right", schema, {"x": right})
        bindings[f"{label}_same"] = call("class", "same", schema, {"x": left})
        observations[f"{label}_language_equal"] = call("eq", left, right)
        observations[f"{label}_fixed_membership"] = call("is", {"x": right}, call("class", "pin", schema, {"x": left}))
        expected.update({f"{label}_language_equal": True, f"{label}_fixed_membership": True})
        observations[f"{label}_exact_repetition"] = outcome(call("extend", "joined",
            [ref(f"{label}_left"), ref(f"{label}_same")], call("record"), {}))
        expected[f"{label}_exact_repetition"] = True
        for direction, parents in (("forward", [ref(f"{label}_left"), ref(f"{label}_right")]),
                                   ("reverse", [ref(f"{label}_right"), ref(f"{label}_left")])):
            for suffix, own, result in (("ambiguous", {}, "conflict"),
                                        ("resolved_left", {"x": left}, True),
                                        ("resolved_right", {"x": right}, "type")):
                key = f"{label}_{direction}_{suffix}"
                observations[key] = outcome(call("extend", "joined", parents, call("record"), own))
                expected[key] = result

    # Default declarations choose data at realization even before a parent has
    # supplied a value. Resolve their disagreement by redeclaring the field.
    for label, value in (("integer", 1), ("float", 1.0)):
        bindings[f"default_{label}"] = call("class", label, call("record", "x", call("default", "any", value)))
    bindings["default_checked"] = call("constrain", ref("default_integer"),
                                       call("fn", ["record"], call("eq", call("type", ref("record.x")), "integer")))
    for direction, parents in (("forward", [ref("default_checked"), ref("default_float")]),
                               ("reverse", [ref("default_float"), ref("default_checked")])):
        key = f"default_{direction}_ambiguous"
        observations[key] = outcome(call("extend", "joined", parents, call("record"), {}))
        expected[key] = "conflict"
        key = f"default_{direction}_resolved"
        observations[key] = outcome(call("extend", "joined", parents,
                                         call("record", "x", call("default", "any", 1)), {}))
        expected[key] = True

    # Explicit contributions retain priority over equal realized defaults. The
    # selected value's representation and semantic provenance must travel together.
    bindings["default_realized"] = call("realize", ref("default_integer"))
    bindings["explicit"] = call("extend", "explicit", [ref("default_integer")], call("record"), {"x": 1.0})
    for direction, parents in (("forward", [ref("default_realized"), ref("explicit")]),
                               ("reverse", [ref("explicit"), ref("default_realized")])):
        name = f"preferred_{direction}"
        bindings[name] = call("realize", call("extend", "joined", parents, call("record"), {}))
        observations[f"{name}_selection"] = [call("type", ref(f"{name}.values.x")),
                              ref(f"{name}.provenance.x.value_kind"), ref(f"{name}.provenance.x.value")]
        expected[f"{name}_selection"] = ["float", "explicit", "explicit"]

    # A lower-priority default must not hide disagreement between two explicit
    # winners. Equal explicit representatives remain valid whichever one is first.
    bindings["explicit_integer"] = call("extend", "integer", [ref("default_integer")], call("record"), {"x": 1})
    bindings["explicit_twin"] = call("extend", "twin", [ref("default_integer")], call("record"), {"x": 1.0})
    for index, positions in enumerate((("default_realized", "explicit", "other"),
                                        ("explicit", "default_realized", "other"),
                                        ("other", "explicit", "default_realized"))):
        for variant, other, expected_result in (("conflict", "explicit_integer", "conflict"),
                                                 ("agree", "explicit_twin", True)):
            parents = [ref(other if name == "other" else name) for name in positions]
            key = f"three_parents_{index}_{variant}"
            observations[key] = outcome(call("extend", "joined", parents, call("record"), {}))
            expected[key] = expected_result

    # Functions can be language-equal while their captured representations change
    # behavior. Repeated exact captures are a positive control against pointer identity.
    bindings["callback"] = call("fn", ["tag"], call("fn", [], call("type", ref("tag"))))
    for name, tag in (("int_callback", 1), ("float_callback", 1.0), ("same_callback", 1)):
        bindings[name] = call("callback", tag)
        bindings[f"{name}_class"] = call("class", name, call("record", "x", "function"), {"x": ref(name)})
    observations["callback_language_equal"] = call("eq", ref("int_callback"), ref("float_callback"))
    observations["callback_behavior"] = [call("int_callback"), call("float_callback")]
    expected.update({"callback_language_equal": True, "callback_behavior": ["integer", "float"]})
    for label, names in (("forward", ("int_callback", "float_callback")),
                          ("reverse", ("float_callback", "int_callback")),
                          ("same", ("int_callback", "same_callback"))):
        key = f"callback_merge_{label}"
        observations[key] = outcome(call("extend", "joined", [ref(f"{name}_class") for name in names], call("record"), {}))
        expected[key] = True if label == "same" else "conflict"
    bindings["callback_resolved"] = call("realize", call("extend", "joined",
        [ref("int_callback_class"), ref("float_callback_class")], call("record"), {"x": ref("int_callback")}))
    observations["callback_resolved_behavior"] = call("callback_resolved.values.x")
    expected["callback_resolved_behavior"] = "integer"

    # Duplicate declaration agreement must still validate a shared malformed
    # field before returning the extended class, even when it has no value yet.
    malformed = {"type": "class", "name": "parent", "parents": [], "concrete": False,
                 "fields": {"x": {"type": "list", "of": None}}, "values": {},
                 "provenance": {"x": {"type": "parent"}}}
    differing = {**malformed, "fields": {"x": "integer"}}
    for label, second, error_kind in (("duplicate", malformed, "type"), ("differing", differing, "conflict")):
        expression = call("extend", "joined", [{"$literal": malformed}, {"$literal": second}], call("record"), {})
        observations[f"malformed_{label}"] = call("try", call("get", expression, "concrete"),
                                                   call("fn", ["error"], ref("error.kind")))
        expected[f"malformed_{label}"] = error_kind

    actual = checked(h, json.dumps(call("let", bindings, observations)))
    assert actual == expected, {key: {"actual": actual.get(key), "expected": value}
                               for key, value in expected.items() if actual.get(key) != value}
