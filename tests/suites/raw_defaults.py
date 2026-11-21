"""Raw declaration defaults are validated even when supplied fields override them."""
import json
import yaml

from tests.suites.contracts import checked
from tests.suites.source_contracts import reference


def call(actor, *args):
    return {"$call": [actor, *args]}


def verify(h):
    bad = {"type": "record", "fields": {
        "x": {"type": "default", "of": "integer", "default": "wrong"}}}
    good = {"type": "record", "fields": {
        "x": {"type": "default", "of": "integer", "default": 7}}}
    nested = {"type": "list", "of": bad}
    handler = call("fn", ["e"], {"$ref": "e"})
    for actor in ("is", "violations", "expect"):
        for value, spec in (({"x": 1}, bad), ([], nested)):
            result = h.helix("run", "-", stdin=json.dumps(call(actor, value, spec)))
            assert result.returncode == 1 and h.malformed_state(result.stdout, 1) is None, result
            error = yaml.safe_load(result.stdout)["error"]
            assert error["kind"] == "type", error
            violation = error["data"]["violations"][0]
            assert violation["expected"] == "integer" and violation["actual"] == "wrong", violation
            assert violation["declaration_path"].endswith("/x/default"), violation
    result = checked(h, json.dumps({
        "unchanged": call("expect", {"x": 1}, good),
        "absent": call("expect", {}, good),
        "valid": call("is", {"x": 1}, good),
        "shape_only": call("subtype", bad, "any"),
        "record": call("try", call("record", "x", bad["fields"]["x"]), handler),
        "class": call("try", call("class", "C", bad, {"x": 1}), handler),
        "replacement": call("with_field", bad, "x", "integer"),
    }))
    assert result["unchanged"] == {"x": 1} and result["absent"] == {}, result
    assert result["valid"] is True and result["shape_only"] is True, result
    assert result["record"]["kind"] == result["class"]["kind"] == "type", result
    assert result["replacement"] == {"type": "record", "fields": {"x": "integer"}}, result
    # Declaration faults blame the supplied schema, never the overriding value.
    for expression, path in ((call("class", "C", bad, {"x": 1}), "/$call/2"),
                             (call("extend", "C", [], bad, {"x": 1}), "/$call/3")):
        process = h.helix("run", "-", stdin=json.dumps(expression))
        assert process.returncode == 1, process
        error = yaml.safe_load(process.stdout)["error"]
        assert error["kind"] == "type" and error["reference"] == reference(path), error
    # The existing engine invokes raw default refinements, including defaults
    # hidden by supplied fields; malformed predicate results remain errors.
    for body, kind in ((False, "type"), (7, "predicate")):
        spec = {"type": "record", "fields": {"x": {
            "type": "default", "of": call("refine", "integer", call("fn", ["v"], body)), "default": 2}}}
        result = checked(h, json.dumps(call("try", call("expect", {"x": 1}, spec), handler)))
        assert result["kind"] == kind, result
    spec = {"type": "record", "fields": {"x": {
        "type": "default", "of": call("refine", "integer", call("fn", ["v"], True)), "default": 2}}}
    assert checked(h, json.dumps(call("expect", {"x": 1}, spec))) == {"x": 1}

    for params in ([], ["a", "b"]):
        predicate = call("fn", params, True)
        raw = {"type": "refine", "of": "integer", "check": predicate}
        for expression in (call("is", "outside", raw), call("subtype", raw, "any"),
                           call("is", [], {"type": "list", "of": raw})):
            error = checked(h, json.dumps(call("try", expression, handler)))
            assert error["kind"] == "type" and "exactly 1" in error["message"], error
        raw_class = {"type": "class", "name": "C", "fields": {}, "values": {},
                     "constraints": [predicate]}
        error = checked(h, json.dumps(call("try", call("subtype", raw_class, "any"), handler)))
        assert error["kind"] == "type" and "exactly 1" in error["message"], error

    # Declaration traversal follows canonical field order, including diagnostics.
    two = {"type": "record", "fields": {
        name: {"type": "default", "of": "integer", "default": name}
        for name in ("z", "a")}}
    error = checked(h, json.dumps(call("try", call("expect", {"a": 1, "z": 2}, two), handler)))
    assert [v["declaration_path"] for v in error["data"]["violations"]] == ["/a/default", "/z/default"], error
    # A malformed declaration stops value checking before a supplied value's
    # refinement can yield an effect. The effect is unreachable, not caught.
    guarded = {"type": "record", "fields": {
        "a": bad["fields"]["x"],
        "z": {"type": "refine", "of": "integer", "check": call("fn", ["v"], call("effect", "time", None))}}}
    result = h.helix("run", "-", stdin=json.dumps(call("expect", {"a": 1, "z": 2}, guarded)))
    state = yaml.safe_load(result.stdout)
    assert result.returncode == 1 and state["status"] == "error", result
    assert state["error"]["kind"] == "type" and "pending_effect" not in state, state
    assert state["error"]["data"]["violations"][0]["declaration_path"] == "/a/default", state
