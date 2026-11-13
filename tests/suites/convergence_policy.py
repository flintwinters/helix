"""Independent comparison oracle for production builtin policy and bounded maps."""
import json

from tests.suites.contracts import checked


def verify(h):
    verify_constraint_realization(h)
    verify_membership_planning_boundary(h)
    wanted = {f"field_{index:03}": None if index % 5 == 0 else index for index in range(128)}
    observed = {key: (value if index % 3 == 0 else "drift")
                for index, (key, value) in enumerate(wanted.items()) if index % 4}
    observed["unmanaged"] = "preserve"
    missing = {key: value for key, value in wanted.items() if key not in observed}
    changed = {key: {"want": value, "have": observed[key]}
               for key, value in wanted.items() if key in observed and observed[key] != value}
    extra = {key: value for key, value in observed.items() if key not in wanted}
    plan = [{"op": "set", "field": key, "to": value,
             **({"from": observed[key]} if key in observed else {})}
            for key, value in wanted.items() if key not in observed or observed[key] != value]
    call = lambda actor, *args: {"$call": [actor, *args]}
    ref = lambda name: {"$ref": name}
    schema = {"type": "record", "fields": {key: "any" for key in wanted}}
    source = {
        "configured": call("realize", call("class", "managed", schema, wanted)),
        "drift": call("drift", ref("configured"), observed),
        "plan": call("converge", ref("configured"), observed),
        "pure": call("type_time", call("converge", ref("configured"), observed)),
        "noop": call("converge", ref("configured"), {**wanted, "unmanaged": "preserve"}),
    }
    result = checked(h, json.dumps(source))
    assert result["drift"] == {"missing": missing, "changed": changed, "extra": extra}, result
    assert result["plan"] == result["pure"] == plan, result
    assert result["noop"] == [], result
    for actor in ("drift", "converge"):
        for configured, observation, kind in ((call("class", "abstract", schema), {}, "abstract"),
                                             (ref("configured"), None, "type")):
            source["invalid"] = call("try", call(actor, configured, observation),
                                     call("fn", ["error"], ref("error")))
            result = checked(h, json.dumps(source))
            assert result["invalid"]["kind"] == kind, result["invalid"]
            assert result["invalid"]["path"].startswith("/invalid/$call/1/$call/"), result["invalid"]
    print("convergence: 128 configured fields, independent plan oracle, purity, no-op and original blame")


def verify_membership_planning_boundary(h):
    """Membership checks permitted values; planning compares configured values."""
    call = lambda actor, *args: {"$call": [actor, *args]}
    ref = lambda name: {"$ref": name}
    configured = call("realize", call("class", "defaulted",
                      call("record", "x", call("default", ref("integer"), 7))))
    cases = [
        ({}, True, {"missing": {"x": 7}, "changed": {}, "extra": {}},
         [{"field": "x", "op": "set", "to": 7}]),
        ({"x": 8}, True, {"missing": {}, "changed": {"x": {"want": 7, "have": 8}}, "extra": {}},
         [{"field": "x", "op": "set", "from": 8, "to": 7}]),
        ({"x": 7, "extra": "keep"}, False, {"missing": {}, "changed": {}, "extra": {"extra": "keep"}}, []),
    ]
    source = call("let", {"configured": configured}, [
        {"member": call("is", observed, ref("configured")),
         "drift": call("drift", ref("configured"), observed),
         "plan": call("converge", ref("configured"), observed)}
        for observed, _, _, _ in cases])
    actual = checked(h, json.dumps(source))
    expected = [{"member": member, "drift": drift, "plan": plan} for _, member, drift, plan in cases]
    assert actual == expected, (actual, expected)


def verify_constraint_realization(h):
    source = (h.root / "tests" / "cases" / "class_constraints_realized.yaml").read_text()
    result = checked(h, source)
    assert result["original_concrete"] is True and result["original_plan"] == [], result
    assert result["rejected_concrete"] is False and result["accepted_concrete"] is False, result
    assert result["values_retained"] is True and result["provenance_retained"] is True, result
    assert result["open_fields"] == {} and result["rejected_membership"] is False, result
    for key in ("pending_drift", "pending_converge", "accepted_pending_converge"):
        assert result[key]["kind"] == "abstract" and "realize it first" in result[key]["message"], result[key]
    error = result["rejected_realization"]
    assert error["kind"] == "type" and "constraint 0 of `service` does not hold" in error["message"], error
    assert result["revalidated_concrete"] is True, result
    assert result["revalidated_plan"] == [{"field": "port", "op": "set", "from": 80, "to": 8080}], result

    call = lambda actor, *args: {"$call": [actor, *args]}
    ref = lambda name: {"$ref": name}
    # Attaching a predicate does not run it. Explicit realization invokes the
    # existing predicate engine, preserving its non-boolean/failure diagnostics.
    for predicate, detail in ((7, "returned integer, expected boolean"),
                              (call("raise", "constraint_probe", "predicate invoked"), "predicate invoked")):
        source = call("let", {
            "base": call("realize", call("class", "probe", call("record"), {})),
            "pending": call("constrain", ref("base"), call("fn", ["values"], predicate)),
        }, {"concrete": ref("pending.concrete"),
            "rechecked": call("try", call("realize", ref("pending")), call("fn", ["error"], ref("error")))})
        result = checked(h, json.dumps(source))
        assert result["concrete"] is False, result
        assert result["rechecked"]["kind"] == "type" and detail in result["rechecked"]["message"], result
