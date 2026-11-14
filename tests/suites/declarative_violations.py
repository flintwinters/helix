"""Independent assertions for actionable, lossless declarative type violations."""
import json
import yaml

from tests.suites.contracts import checked


def call(actor, *args):
    return {"$call": [actor, *args]}


def verify(h):
    spec = {"type": "record", "fields": {
        "absent": "null", "items": {"type": "list", "of": "integer"},
        "present": "integer"}}
    value = {"items": [False, "雪"], "present": None, "extra": 1.0}
    found = checked(h, json.dumps(call("violations", value, spec)))
    assert [(v["at"], v["expected"], v["actual"], v["actual_present"], v["rule"])
            for v in found] == [
        ("/absent", "null", None, False, "required"),
        ("/items/0", "integer", False, True, "type"),
        ("/items/1", "integer", "雪", True, "type"),
        ("/present", "integer", None, True, "type"),
        ("/extra", "never", 1.0, True, "closed_record")], found
    assert type(found[-1]["actual"]) is float
    assert type(found[1]["actual"]) is bool
    # Predicate identity is its source-qualified location, including inline and
    # stacked declarations. Base rejection never invokes the broken predicate.
    source = {
        "positive": call("fn", ["x"], False),
        "broken": call("fn", ["x"], 1),
        "port": call("refine", "integer", {"$ref": "positive"}),
        "bad": call("refine", "integer", {"$ref": "broken"}),
        "rejected": call("violations", 0, {"$ref": "port"}),
        "fault": call("try", call("violations", 1, {"$ref": "bad"}), call("fn", ["e"], {"$ref": "e"})),
        "base": call("violations", "outside", {"$ref": "bad"}),
        "union": call("try", call("violations", 1, call("union", {"$ref": "bad"}, "integer")), call("fn", ["e"], {"$ref": "e"})),
        "skipped": call("violations", 1, call("union", "integer", {"$ref": "bad"})),
    }
    root = checked(h, json.dumps(source))
    for key, rule, path in (("rejected", "refinement", "/positive"),
                            ("fault", "predicate_error", "/broken"),
                            ("union", "predicate_error", "/broken")):
        if key == "rejected":
            violation, = root[key]
        else:
            assert root[key]["kind"] == "predicate", root[key]
            violation, = root[key]["data"]["violations"]
        assert violation["rule"] == rule and violation["actual"] in (0, 1), violation
        predicate = violation["predicate"]
        assert predicate["source"] == "<root>" and predicate["path"] == path, predicate
        assert predicate["reference"] == [path[1:]], predicate
        assert predicate["line"] == 1 and predicate["column"] > 0, predicate
    assert root["base"][0]["rule"] == "type" and "predicate" not in root["base"][0]
    assert root["skipped"] == []
    for actor in ("is", "expect", "violations"):
        result = h.helix("run", "-", stdin=json.dumps(call(actor, 1, call("refine", "integer", call("fn", ["x"], 7)))))
        assert result.returncode == 1 and h.malformed_state(result.stdout, 1) is None, result
        error = yaml.safe_load(result.stdout)["error"]
        assert error["kind"] == "predicate" and error["path"] == "/$call/2/$call/2", error
