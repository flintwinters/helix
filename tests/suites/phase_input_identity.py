"""Type-time input observations preserve representations that evaluation can distinguish."""
import json

import yaml


def verify(h):
    def call(actor, *args):
        return {"$call": [actor, *args]}

    def ref(name):
        return {"$ref": name}

    scenarios = [
        ("numeric_kind", [1, 1.0, 1], call("type", ref("value")), ["integer", "float", "integer"]),
        ("signed_zero", [0.0, -0.0, 0.0],
         call("eq", call("str", ref("value")), call("str", -0.0)), [False, True, False]),
        ("nested_numeric_kind", [{"x": [1]}, {"x": [1.0]}, {"x": [1]}],
         call("type", call("get", ref("value.x"), 0)), ["integer", "float", "integer"]),
    ]
    for name, values, observation, expected in scenarios:
        for captured in (False, True):
            body = call("type_time", observation)
            if captured:
                # The closure comes from one source node, but reads distinct captured values.
                body = call("fn", [], body)
            source = {"sample": call("fn", ["value"], body)}
            for key, value in zip(("a_first", "b_second", "c_repeat"), values):
                expression = call("sample", {"$literal": value})
                source[key] = call(expression) if captured else expression
            for command in ("run", "check"):
                process = h.helix(command, *(["--evaluate"] if command == "check" else []),
                                  "-", stdin=json.dumps(source))
                assert process.returncode == 0 and h.malformed_state(process.stdout, 0) is None, process
                state = yaml.safe_load(process.stdout)
                actual = [state["root"][key] for key in ("a_first", "b_second", "c_repeat")]
                assert actual == expected, (name, captured, command, actual)
                inputs = state.get("type_time_inputs", [])
                assert len(inputs) == 2, (name, captured, command, inputs)
                assert all(row["reads"] and row["reads"][0]["name"].split(".")[0] == "value"
                           for row in inputs), (name, inputs)
