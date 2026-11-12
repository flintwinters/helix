"""Error navigation contracts across source boundaries and error-as-data flows."""
from pathlib import Path
import tempfile

import yaml


def verify(h):
    with tempfile.TemporaryDirectory(dir=h.ROOT / "runtime", prefix="errors-") as directory:
        base = Path(directory)
        (base / "helper.yaml").write_text("leaf: [fn, [], [div, 1, 0]]\nmiddle: [fn, [], [leaf]]\n")
        program = base / "program.yaml"
        program.write_text('m: [import, "helper.yaml"]\nvalue: [m.middle]\n')
        for command in ["run", "check", "recover"]:
            result = h.helix(command, str(program))
            state = yaml.safe_load(result.stdout)
            assert result.returncode == 1 and not result.stderr, result
            error = state["errors"][0] if command == "check" else state["error"]
            inner, outer = error["calls"]
            assert inner["file"].endswith("helper.yaml") and inner["path"] == "/middle/2", inner
            assert "file" not in outer and outer["path"] == "/value", outer
            assert [frame["function"] for frame in [inner, outer]] == ["/leaf", "/middle"]
            for frame, line, column in [(inner, 1, 7), (outer, 2, 9)]:
                assert frame["function_file"].endswith("helper.yaml"), frame
                assert (frame["function_line"], frame["function_column"]) == (line, column), frame
        # Caught callback errors and re-raises retain the same navigable definition data.
        program.write_text('m: [import, "helper.yaml"]\nvalue: [try, [map, m.leaf, [1]], [fn, [e], [raise, e]]]\n')
        result = h.helix("run", str(program))
        error = yaml.safe_load(result.stdout)["error"]
        cause = error["cause"]
        assert cause["kind"] == "arity" and cause["calls"][0]["function"] == "/leaf", cause
        assert cause["calls"][0]["function_file"].endswith("helper.yaml"), cause
        handler = error["calls"][0]
        assert handler["function"] == "/value/2" and "function_file" not in handler, handler
        assert (handler["function_line"], handler["function_column"]) == (2, 34), handler
