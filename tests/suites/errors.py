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

    verify_hints(h)


# Each rejected spelling, the error it raises, and the correction its hint names. `static`
# marks errors that `check` reports without executing, with the same message and hint.
HINTS = [
    ("total: !call [fold, [1, 2], 0, !ref add]", "unbound", "/total/$call/3", "unbound symbol `add`",
     "`add` is a builtin actor, not a value: call it as `!call [add, ...]`, or wrap it to pass it as a function: `!call [fn, [a, b], !call [add, !ref a, !ref b]]`", True),
    ("counter: 3\ny: !call [add, !ref countr, 1]", "unbound", "/y/$call/1", "unbound symbol `countr`",
     "did you mean `counter`?", True),
    ("v: !call [mapp, [1]]", "unbound", "/v/$call/0", "unbound actor `mapp`", "did you mean `map`?", True),
    ("z: !call [clamp, 5, 1, 3]", "unbound", "/z/$call/0", "unbound actor `clamp`",
     "`clamp` is exported by std:math: bind `math: !call [import, 'std:math']` and write `!call [math.clamp, ...]`", True),
    ("w: !ref math.max", "unbound", "/w", "unbound symbol `math.max`",
     "bind the module first: `math: !call [import, 'std:math']`", True),
    ("math: !call [import, 'std:math']\nb: !call [math.maxx, 3, 9]", "unbound", "/b/$call/0", "unbound actor `math.maxx`",
     "`math` has no member `maxx`; did you mean `math.max`?", False),
    ("cfg: {port: 80}\nb: !ref cfg.prot", "unbound", "/b", "unbound symbol `cfg.prot`",
     "`cfg` has no member `prot`; did you mean `cfg.port`?", False),
    ("cfg: {port: 80}\nc: !ref cfg.port.x", "unbound", "/c", "unbound symbol `cfg.port.x`",
     "`cfg.port` is an integer, not a map, so it has no member `x`", False),
    ("math: !call [import, 'std:math']\na: !call [math, 3, 9]", "type", "/a/$call/0", "call actor `math` must resolve to a function, got map",
     "`math` is a map; call one of its function members as `!call [math.abs, ...]`; members: abs, clamp, max, min, number, sign, square", False),
    ("x: !if [true, 1, 2]", "unsupported", None, "unsupported YAML tag `!if`", "`if` is a builtin actor; write `!call [if, ...]`", True),
    ("a: !!binary aGVsbG8=", "unsupported", None, "unsupported YAML tag `!!binary`",
     "Helix tags are `!call`, `!ref` and `!literal`; plain YAML values need no tag", True),
    ("a: !call {add: [1, 2]}", "parse", None, "`!call` requires a sequence such as `!call [add, 1, 2]`, got a mapping", None, True),
    ("a: !ref [x]", "parse", None, "`!ref` requires a scalar name such as `!ref x`, got a sequence", None, True),
]


# Where each checking mode reports its first rejection.
REPORTED = {("check", "--evaluate"): "errors", ("check",): "diagnostics"}


def verify_hints(h):
    """Rejections name the correction; hints are advisory and leave kind, message and blame alone."""
    for source, kind, path, message, hint, static in HINTS:
        commands = [("run",), ("check", "--evaluate")] + ([("check",)] if static else [])
        for command in commands:
            result = h.helix(*command, "-", stdin=source)
            state = yaml.safe_load(result.stdout)
            assert result.returncode == 1, (source, command, result)
            error = state["error"] if command == ("run",) else state[REPORTED[command]][0]
            observed = (error["kind"], error.get("path") if path else None, error["message"], error.get("hint"))
            assert observed == (kind, path, message, hint), (source, command, error)
