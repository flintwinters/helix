"""Public constructor parity, source blame and bounded descriptor assembly costs."""
import json

import yaml

from tests.suites.contracts import checked


def verify(h):
    constructors = [
        (["list_of", "integer"], {"type": "list", "of": "integer"}),
        (["map_of", "string"], {"type": "map", "of": "string"}),
        (["optional", "boolean"], {"type": "optional", "of": "boolean"}),
        (["default", "integer", 9], {"type": "default", "of": "integer", "default": 9}),
        (["union", "integer", "null"], {"type": "union", "of": ["integer", "null"]}),
        (["enum", 1, None, 1], {"type": "enum", "values": [1, None, 1]}),
        (["literal", {"x": None}], {"type": "literal", "value": {"x": None}}),
        (["secret", "credentials/test"], {"type": "secret", "ref": "credentials/test"}),
        (["record"], {"type": "record", "fields": {}}),
        (["with_field", {"type": "record", "fields": {"x": "integer"}}, "x", "string"],
         {"type": "record", "fields": {"x": "string"}}),
        # Replacement removes the old declaration; only surviving fields need
        # to be valid, so replacing the malformed field remains a valid repair.
        (["with_field", {"type": "record", "fields": {"bad": 3, "kept": "boolean"}}, "bad", "integer"],
         {"type": "record", "fields": {"bad": "integer", "kept": "boolean"}}),
    ]
    for count in (1, 32, 256):
        fields = {f"field_{index:03}": "integer" for index in range(count)}
        constructors.append((["record", *(item for pair in fields.items() for item in pair)],
                             {"type": "record", "fields": fields}))
        constructors.append((["types.from_fields", fields], {"type": "record", "fields": fields}))
    source = {f"c{index}": {"$call": args} for index, (args, _) in enumerate(constructors)}
    source = {"$call": ["let", {"types": {"$call": ["import", "std:types"]}}, source]}
    assert checked(h, json.dumps(source)) == {
        f"c{index}": expected for index, (_, expected) in enumerate(constructors)
    }

    invalid = [
        (["list_of", 3], 1, "type", "expected a type"),
        (["map_of", {"type": "list"}], 1, "type", "of"),
        (["optional", 3], 1, "type", "expected a type"),
        (["default", "integer", "bad"], 2, "type", "default does not fit integer"),
        (["union", "integer", 3], 2, "type", "expected a type"),
        (["secret", ""], 1, "type", "non-empty string reference"),
        (["record", "x"], None, "arity", "alternating field names and types"),
        (["record", 1, "integer"], 1, "type", "string field name"),
        (["record", "x", "integer", "x", "string"], 3, "type", "duplicate field `x`"),
        (["record", "x", 3], 2, "type", "expected a type"),
        (["with_field", 1, "x", "integer"], 1, "type", "record type"),
        (["with_field", {"type": "class"}, "x", "integer"], 1, "type", "classes grow with"),
        (["with_field", {"type": "record", "fields": {}}, 1, "integer"], 2, "type", "string field name"),
        (["with_field", {"type": "record", "fields": {}}, "x", 3], 3, "type", "expected a type"),
        (["with_field", {"type": "record"}, "x", "integer"], 1, "type", "malformed record type"),
    ]
    # Adding one valid field cannot carry malformed surviving schemas into a
    # supposedly constructed record. The invalid input is the original record.
    for spec in (3, {"type": "optional", "of": 3},
                 {"type": "default", "of": 3, "default": 0},
                 {"type": "list", "of": 3},
                 {"type": "record", "fields": {"nested": 3}}):
        invalid.append((["with_field", {"type": "record", "fields": {"bad": spec}},
                         "good", "string"], 1, "type", "expected a type"))
    for args, blame, kind, message in invalid:
        result = h.helix("run", "-", stdin=json.dumps({"probe": {"$call": args}}))
        assert result.returncode == 1 and h.malformed_state(result.stdout, 1) is None, result
        error = yaml.safe_load(result.stdout)["error"]
        assert error["kind"] == kind and message in error["message"], (args, error)
        assert error["path"] == ("/probe" if blame is None else f"/probe/$call/{blame}"), (args, error)
    print(f"descriptor policy: {len(constructors)} value/size cases and {len(invalid)} original-source faults")
