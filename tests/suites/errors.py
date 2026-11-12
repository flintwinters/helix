"""Error navigation contracts across source boundaries and error-as-data flows."""
from pathlib import Path
import tempfile

import yaml
from tests.suites.source_contracts import location


def verify(h):
    with tempfile.TemporaryDirectory(dir=h.root / "runtime", prefix="errors-") as directory:
        base = Path(directory)
        (base / "helper.yaml").write_text('{"leaf": {"$call": ["fn", [], {"$call": ["div", 1, 0]}]}, "middle": {"$call": ["fn", [], {"$call": ["leaf"]}]}}')
        program = base / "program.yaml"
        program.write_text('{"m": {"$call": ["import", "helper.yaml"]}, "value": {"$call": ["m.middle"]}}')
        for command in ["run", "check", "recover"]:
            result = h.helix(command, *(["--evaluate"] if command == "check" else []), str(program))
            state = yaml.safe_load(result.stdout)
            assert result.returncode == 1 and not result.stderr, result
            error = state["errors"][0] if command == "check" else state["error"]
            inner, outer = error["calls"]
            assert inner["file"].endswith("helper.yaml") and inner["path"] == "/middle/$call/2", inner
            assert "file" not in outer and outer["path"] == "/value", outer
            assert [frame["function"] for frame in [inner, outer]] == ["/leaf", "/middle"]
            for frame, definition in [(inner, "/leaf"), (outer, "/middle")]:
                assert frame["function_file"].endswith("helper.yaml"), frame
                assert (frame["function_line"], frame["function_column"]) == location((base / "helper.yaml").read_text(), definition), frame
        # Caught callback errors and re-raises retain the same navigable definition data.
        program.write_text('{"m": {"$call": ["import", "helper.yaml"]}, "value": {"$call": ["try", {"$call": ["map", {"$ref": "m.leaf"}, [1]]}, {"$call": ["fn", ["e"], {"$call": ["raise", {"$ref": "e"}]}]}]}}')
        result = h.helix("run", str(program))
        error = yaml.safe_load(result.stdout)["error"]
        cause = error["cause"]
        assert cause["kind"] == "arity" and cause["calls"][0]["function"] == "/leaf", cause
        assert cause["calls"][0]["function_file"].endswith("helper.yaml"), cause
        handler = error["calls"][0]
        assert handler["function"] == "/value/$call/2" and "function_file" not in handler, handler
        assert (handler["function_line"], handler["function_column"]) == location(program.read_text(), "/value/$call/2"), handler
