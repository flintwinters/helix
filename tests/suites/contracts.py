"""Bounded contract suites, orchestrated only by manage.py test."""
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml


def checked(h, source):
    result = h.helix("run", "-", stdin=source)
    why = h.malformed_state(result.stdout, result.returncode)
    assert why is None, f"{why}; stderr={result.stderr}"
    state = yaml.safe_load(result.stdout)
    assert state["status"] == "finished", state
    return state["root"]


def harness(h):
    for output, code in [("", 0), ("[", 0), ("status: ready", 0),
                         ("status: finished", 0), ("status: error", 1),
                         ("status: finished\nroot: 1", 1), ("", -11)]:
        assert h.malformed_state(output, code), (output, code)
    assert h.malformed_state("status: finished\nroot: null", 0) is None
    assert h.malformed_state("status: error\nerror: {kind: type, message: bad, line: 1, column: 2}", 1) is None
    assert h.root_section("status: finished\nroot:\n  a: 1\n") == "a: 1\n"
    assert h.root_section("status: error\n") is None
    assert "" in list(h.mutations("a: 1\nb: 2\n"))
    with patch.object(h.subprocess, "run", side_effect=subprocess.TimeoutExpired("helix", 1)):
        result = h.helix("run", "-", stdin="1")
        assert result.returncode == 124 and "timed out" in result.stderr
    # Exercise actual timeout and crash handling without running a second harness entrypoint.
    with patch.object(h, "BIN", Path(sys.executable)), patch.dict("os.environ", {"HELIX_TEST_TIMEOUT": "0.05"}):
        assert h.helix("-c", "import time; time.sleep(5)").returncode == 124
        assert h.malformed_state("", h.helix("-c", "raise SystemExit(7)").returncode)
    cases = [c for directory in h.COMMANDS for c in directory.glob("*.yaml") if not c.name.endswith(".expected.yaml")]
    assert cases
    assert all(c.with_suffix(".expected.yaml").is_file() for c in cases)
    assert all(p.with_name(p.name.replace(".expected.yaml", ".yaml")).is_file()
               for d in h.COMMANDS for p in d.glob("*.expected.yaml"))


def cli(h):
    for args in [(), ("unknown",), ("run",), ("run", "-", "extra"), ("check",), ("std", "unknown")]:
        result = h.helix(*args)
        assert result.returncode == 2 and not result.stdout and result.stderr, (args, result)
    missing = h.helix("run", "runtime/definitely-missing-helix-input.yaml")
    assert missing.returncode == 2 and not missing.stdout and "cannot read" in missing.stderr
    with tempfile.TemporaryDirectory(dir=h.ROOT / "runtime", prefix="cli-") as directory:
        source = Path(directory) / "program with spaces.yaml"
        for command in ("run", "check"):
            for program, code in [("answer: [add, 20, 22]\n", 0), ("answer: [div, 1, 0]\n", 1), ("[", 1)]:
                source.write_text(program)
                file = h.helix(command, str(source))
                stdin = h.helix(command, "-", stdin=program)
                assert file.returncode == stdin.returncode == code
                assert file.stdout == stdin.stdout and not file.stderr and not stdin.stderr
                assert h.malformed_state(file.stdout, code) is None


def expression(rng, depth):
    if depth == 0 or rng.randrange(3) == 0:
        value = rng.randint(-20, 20)
        return str(value), value
    left, a = expression(rng, depth - 1)
    right, b = expression(rng, depth - 1)
    op = rng.choice(("add", "sub", "mul", "min", "max"))
    oracle = {"add": lambda: a + b, "sub": lambda: a - b, "mul": lambda: a * b,
              "min": lambda: min(a, b), "max": lambda: max(a, b)}
    head = f"m.{op}" if op in ("min", "max") else op
    return f"[{head}, {left}, {right}]", oracle[op]()


def generated(h):
    seed = 714209
    rng = random.Random(seed)
    # Independent integer-tree oracle; deliberately bounded far below i64 overflow.
    programs = [expression(rng, 4) for _ in range(100)]
    source = "m: [import, std:math]\n" + "".join(f"v{i}: {p}\n" for i, (p, _) in enumerate(programs))
    root = checked(h, source)
    for i, (_, expected) in enumerate(programs):
        assert root[f"v{i}"] == expected, f"seed={seed} expression={programs[i]}"
    for i in range(25):
        xs = [rng.randint(-12, 12) for _ in range(rng.randrange(12))]
        values = "[list" + "".join(f", {x}" for x in xs) + "]"
        root = checked(h, f"l: [import, std:list]\nxs: {values}\ns: [l.sum, xs]\nr: [l.reverse, xs]\nrr: [l.reverse, [l.reverse, xs]]\np: [add, [l.sum, xs], 0]\n")
        assert root["s"] == root["p"] == sum(xs), f"seed={seed} sample={i}"
        assert root["r"] == xs[::-1] and root["rr"] == xs
    literals = [None, True, False, 0, -9223372036854775808, 9223372036854775807,
                "add", "true", "null", "1", "é雪🙂", "line\nbreak", "", "a: b", "'quoted'", "\\"]
    for _ in range(30):
        value = {"value": [rng.choice(literals) for _ in range(rng.randrange(8))], "nested": {"text": rng.choice(literals)}}
        # Quote all strings so generated source cannot introduce symbols or expressions.
        source = json.dumps(value, ensure_ascii=False)
        for cycle in range(3):
            result = h.helix("run", "-", stdin=source)
            assert h.malformed_state(result.stdout, result.returncode) is None
            assert yaml.safe_load(result.stdout)["root"] == value, f"seed={seed} cycle={cycle} source={source}"
            next_source = h.root_section(result.stdout)
            if cycle:
                assert next_source == source
            source = next_source


def imports(h):
    with tempfile.TemporaryDirectory(dir=h.ROOT / "runtime", prefix="imports-") as directory:
        base = Path(directory)
        (base / "shared.yaml").write_text("value: 21\n")
        for name in ("left", "right"):
            (base / f"{name}.yaml").write_text("s: [import, shared.yaml]\nvalue: [add, s.value, 0]\n")
        main = base / "main.yaml"
        main.write_text("l: [import, left.yaml]\nr: [import, right.yaml]\nanswer: [add, l.value, r.value]\n")
        result = h.helix("run", str(main))
        assert result.returncode == 0 and yaml.safe_load(result.stdout)["root"]["answer"] == 42
        for content, kind in [("[", "parse"), ("value: [add, missing, 1]\n", "unbound")]:
            (base / "shared.yaml").write_text(content)
            result = h.helix("run", str(main))
            assert h.malformed_state(result.stdout, result.returncode) is None
            error = yaml.safe_load(result.stdout)["error"]
            assert error["kind"] == kind and error["file"].endswith("shared.yaml"), error
            assert error["line"] == (2 if kind == "parse" else 1), error
        (base / "shared.yaml").write_text("again: [import, main.yaml]\n")
        result = h.helix("run", str(main))
        assert result.returncode == 1 and h.malformed_state(result.stdout, 1) is None
        (base / "shared.yaml").unlink()
        result = h.helix("run", str(main))
        assert result.returncode == 1 and h.malformed_state(result.stdout, 1) is None
        # A module cannot capture the importing file's lexical bindings.
        (base / "shared.yaml").write_text("value: [add, private, 1]\n")
        main.write_text("private: 9\ns: [import, shared.yaml]\n")
        error = yaml.safe_load(h.helix("run", str(main)).stdout)["error"]
        assert error["kind"] == "unbound" and error["path"] == "/value/1", error


def run(name, h):
    try:
        globals()[name](h)
        print(f"{name}: passed (generated seed=714209)" if name == "generated" else f"{name}: passed")
        return []
    except Exception as error:
        import traceback
        return [f"{name}: {error}\n{traceback.format_exc()}\nreproduce: ./manage.py test --suite {name}"]
