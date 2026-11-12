"""Independent comparison oracle for production builtin policy and bounded maps."""
import json

from tests.suites.contracts import checked


def verify(h):
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
