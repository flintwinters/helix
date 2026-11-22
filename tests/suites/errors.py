"""Error navigation contracts across source boundaries and error-as-data flows."""
from pathlib import Path
import tempfile

import yaml
from tests.suites.source_contracts import location, reference


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
            assert inner["file"].endswith("helper.yaml") and inner["reference"] == ["middle", "$call", 2], inner
            assert "file" not in outer and outer["reference"] == ["value"], outer
            for frame, definition in [(inner, "/leaf"), (outer, "/middle")]:
                assert frame["function_file"].endswith("helper.yaml"), frame
                assert (frame["function_line"], frame["function_column"]) == location((base / "helper.yaml").read_text(), definition), frame
        # Caught callback errors and re-raises retain the same navigable definition data.
        program.write_text('{"m": {"$call": ["import", "helper.yaml"]}, "value": {"$call": ["try", {"$call": ["map", {"$ref": "m.leaf"}, [1]]}, {"$call": ["fn", ["e"], {"$call": ["raise", {"$ref": "e"}]}]}]}}')
        result = h.helix("run", str(program))
        error = yaml.safe_load(result.stdout)["error"]
        cause = error["cause"]
        assert cause["kind"] == "arity" and cause["calls"][0]["function_line"] == 1, cause
        assert cause["calls"][0]["function_file"].endswith("helper.yaml"), cause
        handler = error["calls"][0]
        assert "function_file" not in handler, handler
        assert (handler["function_line"], handler["function_column"]) == location(program.read_text(), "/value/$call/2"), handler

    verify_hints(h)
    verify_recorded_repairs(h)


# Each rejected spelling, the error it raises, and the correction its hint names. `static`
# marks errors that `check` reports without executing, with the same message and hint.
HINTS = [
    ("total: !call [fold, [1, 2], 0, !ref add]", "unbound", "/total/$call/3", "unbound symbol `add`",
     "`add` is a builtin actor, not a value: call it as `!call [add, a, b, more...]`, or wrap it to pass it as a function: `!call [fn, [a, b], !call [add, !ref a, !ref b]]`", True),
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
    ("m: {a: 0, f: !call [fn, [], 1]}\nx: !call [m]", "type", "/x/$call/0",
     "call actor `m` must resolve to a function, got map",
     "`m` is a map; call one of its function members as `!call [m.f, ...]`; members: a, f", False),
    ("m: {a: 0}\nx: !call [m]", "type", "/x/$call/0",
     "call actor `m` must resolve to a function, got map",
     "`m` is a map; select a function as the call actor; members: a", False),
    ("x: !call [source_subtype, a, b]", "type", "/x/$call/1",
     "`source_subtype` argument 1: unknown type `a`; use a primitive (any, never, integer, float, string, boolean, null, list, map, secret, function, vm) or pass a type descriptor",
     None, False),
    ("x: !call [source_subtype, interger, integer]", "type", "/x/$call/1",
     "`source_subtype` argument 1: unknown type `interger`; use `integer` if you meant that primitive type", None, False),
    ("x: !call [is, 1, !call [var, t]]", "type", "/x/$call/2",
     "`is` argument 2: type variable `t` is not bound by an enclosing `rec`; use the name of an enclosing binder or wrap the descriptor in `!call [rec, t, body]`",
     None, False),
    ("a: !call [add, !ref b, 1]\nb: !call [add, !ref a, 1]", "cycle", None,
     "value depends on itself",
     "evaluating this value reads it again through the references it contains; compute one value in the loop from inputs that do not depend on it, or recurse through a function", False),
    ("x: !if [true, 1, 2]", "unsupported", None, "unsupported YAML tag `!if`", "`if` is a builtin actor; write `!call [if, ...]`", True),
    ("a: !!binary aGVsbG8=", "unsupported", None, "unsupported YAML tag `!!binary`",
     "Helix tags are `!call`, `!ref` and `!literal`; plain YAML values need no tag", True),
    ("a: !call {add: [1, 2]}", "parse", None, "`!call` requires a sequence such as `!call [add, 1, 2]`, got a mapping", None, True),
    ("a: !ref [x]", "parse", None, "`!ref` requires a scalar name such as `!ref x`, got a sequence", None, True),
    # YAML syntax errors name the structural cause, not only the scanner state.
    ("a: [1, 2\nb: 3", "parse", None, "illegal placement of ':' indicator",
     "the `[` at line 1 column 4 is never closed; add `]` where the collection ends, or write it in block style", True),
    ('a: "x [y"\nb: {c: 2', "parse", None, "while parsing a flow mapping, did not find expected ',' or '}'",
     "the `{` at line 2 column 4 is never closed; add `}` where the collection ends, or write it in block style", True),
    ("a:\n\tb: 1", "parse", None, "tabs disallowed within this context (block indentation)",
     "indent with spaces; YAML forbids tab characters in indentation", True),
    ("a: b: c", "parse", None, "mapping values are not allowed in this context",
     "a plain value cannot contain `: `; quote it (`key: \"a: b\"`) or move the nested mapping to its own indented line", True),
    ("a:\n  b: 1\n c: 2", "parse", None, "while parsing a block mapping, did not find expected key",
     "align sibling keys at the same column using spaces, and indent a nested value deeper than its key", True),
    # Rejected values: the operand is named, with expected versus actual and a bounded excerpt.
    ('a: !call [add, 1, "x"]', "type", "/a/$call/2", '`add` argument 2: expected number, got string "x"', None, False),
    ("a: !call [if, 3, 1, 2]", "type", "/a/$call/1", "`if` condition: expected boolean, got integer 3", None, False),
    ("b: !call [map, [1], !call [fn, [x], !ref x]]", "type", "/b/$call/2", "`map` list: expected list, got function",
     "iteration takes the function first: `!call [map, f, list]`", False),
    ("a: !call [get, {port: 80}, prot]", "missing", "/a/$call/2", "map has no key `prot`",
     "did you mean `port`? Otherwise pass a default as the third argument to tolerate absence", False),
    ("a: !call [get, [1, 2, 3], 7]", "missing", "/a/$call/2", "index 7 is out of range for a list of length 3",
     "pass a default as the third argument to tolerate absence", False),
    ("f: !call [fn, [x], !ref x]\na: !call [f, 1, 2]", "arity", "/a", "the function defined at 1:10 takes 1 argument (x), got 2", None, False),
    ("f: !call [fn, x, !ref x]", "type", "/f/$call/1", "`fn` parameters must be a list of names or `[name, type]` pairs",
     "write the parameters as a list, even for one: `!call [fn, [x], body]`", False),
    ("f: !call [fn, [x, [y, integer, 3]], !ref x]", "type", "/f/$call/1/1", "`fn` parameter 2 must be a name or a `[name, type]` pair",
     "write `x` or `[x, integer]`; a default or extra entry belongs in the body", False),
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
            observed = (error["kind"], error.get("reference") if path else None, error["message"], error.get("hint"))
            assert observed == (kind, reference(path) if path else None, message, hint), (source, command, error)
    # A caught error value carries the same hint as the uncaught report.
    caught = "a: !call [try, !call [get, {port: 80}, prot], !call [fn, [e], !call [get, !ref e, hint]]]"
    result = h.helix("run", "-", stdin=caught)
    assert yaml.safe_load(result.stdout)["root"]["a"].startswith("did you mean `port`?"), result


def verify_recorded_repairs(h):
    """Replay the malformed flow sequences preserved in the workspace benchmark."""
    evidence = h.root / "benchmarks/agent/workspace-repairs-v3.yaml"
    for repair in yaml.safe_load(evidence.read_text())["repairs"]:
        write, failure, *_ = repair["events"]
        assert write["action"] == "write" and failure["action"] == "execute-public-test", repair
        source = write["arguments"]["content"]
        recorded = yaml.safe_load(failure["stdout"])["error"]
        assert recorded["kind"] == "parse" and failure["exit_code"] == 1, repair
        for command, field in [(("run",), "error"), (("check",), "diagnostics")]:
            result = h.helix(*command, "-", stdin=source)
            state = yaml.safe_load(result.stdout)
            error = state[field] if field == "error" else state[field][0]
            assert result.returncode == 1 and error["kind"] == "parse", (command, error)
            assert "add `]` where the collection ends, or write it in block style" in error["hint"], error
