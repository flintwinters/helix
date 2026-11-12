"""Bounded contract suites, orchestrated only by manage.py test."""
import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path
from dataclasses import replace

import yaml


def checked(h, source):
    result = h.helix("run", "-", stdin=source)
    why = h.malformed_state(result.stdout, result.returncode)
    assert why is None, f"{why}; stderr={result.stderr}"
    state = yaml.safe_load(result.stdout)
    assert state["status"] == "finished", state
    return state["root"]


def harness(h):
    import contextlib
    import io
    from unittest.mock import patch
    from tooling import test_runner
    # Regression orchestration must keep failures from every selected suite.
    with patch.object(test_runner, "SUITES", {"first": "probe", "second": "probe"}), \
         patch.object(sys.modules[__name__], "run", side_effect=[["probe failure"], []]) as dispatch, \
         contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
        assert h.regression(False, ["first", "second"]) == 1
        assert [call.args[0] for call in dispatch.call_args_list] == ["first", "second"]
    with patch.object(test_runner, "SUITES", {"first": "probe", "second": "probe"}), \
         patch.object(sys.modules[__name__], "run", side_effect=[SystemExit(2), []]) as dispatch, \
         contextlib.redirect_stderr(io.StringIO()) as output, contextlib.redirect_stdout(io.StringIO()):
        assert h.regression(False, ["first", "second"]) == 1
        assert [call.args[0] for call in dispatch.call_args_list] == ["first", "second"]
        assert "SystemExit" in output.getvalue()

    from tests.suites.state_validation import verify as verify_states
    verify_states(h)
    from tests.suites.test_runner import verify as verify_runner
    verify_runner(h)
    from tests.suites.mutation_generation import verify as verify_mutations
    verify_mutations(h)
    from tests.suites.test_reports import verify as verify_reports
    verify_reports(h)
    for output, code in [("", 0), ("[", 0), ("status: ready", 0),
                         ("status: finished", 0), ("status: error", 1),
                         ("status: finished\nroot: 1", 1), ("", -11)]:
        assert h.malformed_state(output, code), (output, code)
    assert h.malformed_state("status: finished\nroot: null", 0) is None
    assert h.malformed_state("status: error\nerror: {kind: type, message: bad, line: 1, column: 2}", 1) is None
    assert h.root_section("status: finished\nroot:\n  a: 1\n") == "a: 1\n"
    assert h.root_section("status: finished\nroot:\n  a: 1\nmode: check\n") == "a: 1\n"
    assert h.root_section("root: 42\nstatus: finished\n") == "42\n"
    assert h.root_section("status: error\n") is None
    assert h.root_section("status: finished\nroot: {ref: function1}\nobjects: {}\n") is None
    for scalar in ("1", "null", "false", "'quoted'", "[]", "{}"):
        assert h.root_section(f"status: finished\nroot: {scalar}\n") == scalar + "\n"
    assert h.root_section("status: finished\nroot: |-\n  first\n  second\n") == "|-\n  first\n  second\n"
    for separator in ("\x85", "\u2028", "\u2029"):
        scalar = f'"first{separator}second"'
        assert h.root_section(f"status: finished\nroot: {scalar}\n") == scalar + "\n"
    assert "" in list(h.mutations("a: 1\nb: 2\n"))
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
    timed_out = replace(h, process_runner=timeout)
    result = timed_out.helix("run", "-", stdin="1")
    assert result.returncode == 124 and "timed out" in result.stderr
    # Exercise actual timeout and crash handling without changing the caller's executable.
    python = replace(h, binary=Path(sys.executable), timeout=0.05)
    assert python.helix("-c", "import time; time.sleep(5)").returncode == 124
    assert h.malformed_state("", python.helix("-c", "raise SystemExit(7)").returncode)
    from tooling.test_runner import discover_cases
    cases = discover_cases(h.commands, h.root)
    assert cases
    assert all(c.with_suffix(".expected.yaml").is_file() for c in cases)
    assert all(p.with_name(p.name.replace(".expected.yaml", ".yaml")).is_file()
               for d in h.commands for p in d.glob("*.expected.yaml"))


    import contextlib
    import io
    with tempfile.TemporaryDirectory(dir=h.root / "runtime", prefix="harness-") as directory:
        case = Path(directory) / "probe.yaml"
        case.write_text("value: 42")
        expected = "status: finished\nroot:\n  value: 42\n"
        case.with_suffix(".expected.yaml").write_text(expected)
        def fake(args, **kwargs):
            if args[1] == "std":
                return h.process_runner(args, **kwargs)
            return subprocess.CompletedProcess(args, 0, expected, "")
        fixture = replace(h, commands={case.parent: "run"}, process_runner=fake)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            assert fixture.test(False, ["golden"]) == 0
            case.with_suffix(".expected.yaml").write_text(expected.replace("42", "43"))
            assert fixture.test(False, ["golden"]) == 1, "golden comparator accepted a semantic fault"
            case.with_suffix(".expected.yaml").write_text(expected)
            def broken_reload(args, **kwargs):
                result = fake(args, **kwargs)
                if kwargs.get("input") is not None:
                    result.stdout = expected.replace("42", "43")
                return result
            broken = replace(fixture, process_runner=broken_reload)
            assert broken.test(False, ["golden"]) == 1, "round-trip comparator accepted broken emission"
            assert fixture.test(False, ["golden"]) == 0, "derived context changed the original runner"


def cli(h):
    from tests.suites.tooling_cli import verify as verify_tooling_cli
    verify_tooling_cli(h)
    from tests.suites.recovery import verify as verify_recovery
    verify_recovery(h)
    from tests.suites.inventory import verify
    verify(h)
    for args in [(), ("unknown",), ("unknown", "--help"), ("run",), ("run", "-", "extra"), ("check",), ("std", "unknown")]:
        result = h.helix(*args)
        assert result.returncode == 2 and not result.stdout and result.stderr, (args, result)
    for command in ("run", "check", "fmt", "step", "trace", "inspect", "watch", "repl", "lsp", "schema", "completion"):
        help_result = h.helix(command, "--help")
        assert help_result.returncode == 0 and "usage:" in help_result.stdout and not help_result.stderr
    missing = h.helix("run", "runtime/definitely-missing-helix-input.yaml")
    assert missing.returncode == 2 and not missing.stdout and "cannot read" in missing.stderr
    with tempfile.TemporaryDirectory(dir=h.root / "runtime", prefix="cli-") as directory:
        source = Path(directory) / "program with spaces.yaml"
        for command in ("run", "check"):
            for program, code in [('{"answer": {"$call": ["add", 20, 22]}}', 0), ('{"answer": {"$call": ["div", 1, 0]}}', 1), ("[", 1)]:
                source.write_text(program)
                file = h.helix(command, *(["--evaluate"] if command == "check" else []), str(source))
                stdin = h.helix(command, *(["--evaluate"] if command == "check" else []), "-", stdin=program)
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
    return f'{{"$call": ["{head}", {left}, {right}]}}', oracle[op]()


def literal_meaning(value):
    """Include scalar kinds: Python equality alone conflates True, 1 and 1.0."""
    if isinstance(value, dict):
        return {key: literal_meaning(item) for key, item in value.items()}
    if isinstance(value, list):
        return [literal_meaning(item) for item in value]
    return type(value).__name__, value


def generated(h):
    seed = 714209
    rng = random.Random(seed)
    # Independent integer-tree oracle; deliberately bounded far below i64 overflow.
    programs = [expression(rng, 4) for _ in range(100)]
    source = 'm: {$call: [import, "std:math"]}\n' + "".join(f"v{i}: {p}\n" for i, (p, _) in enumerate(programs))
    root = checked(h, source)
    for i, (_, expected) in enumerate(programs):
        assert root[f"v{i}"] == expected, f"seed={seed} expression={programs[i]}"
    assert checked(h, '{"$call": ["eq", {"$call": ["add", 0.5, 0.25]}, 0.75]}') is True
    assert checked(h, '{"$call": ["if", true, {"$call": ["add", 1, 0]}, {"$call": ["div", 1, 0]}]}') == 1
    overflow = h.helix("run", "-", stdin='{"$call": ["add", 9223372036854775807, 1]}')
    assert overflow.returncode == 1
    assert yaml.safe_load(overflow.stdout)["error"]["kind"] == "overflow"
    for i in range(25):
        xs = [rng.randint(-12, 12) for _ in range(rng.randrange(12))]
        values = json.dumps(xs)
        root = checked(h, f'{{"l": {{"$call": ["import", "std:list"]}}, "xs": {values}, "s": {{"$call": ["l.sum", {{"$ref": "xs"}}]}}, "r": {{"$call": ["l.reverse", {{"$ref": "xs"}}]}}, "rr": {{"$call": ["l.reverse", {{"$call": ["l.reverse", {{"$ref": "xs"}}]}}]}}, "p": {{"$call": ["add", {{"$call": ["l.sum", {{"$ref": "xs"}}]}}, 0]}}}}')
        assert root["s"] == root["p"] == sum(xs), f"seed={seed} sample={i}"
        assert root["r"] == xs[::-1] and root["rr"] == xs
    literals = [None, True, False, 0, 0.5, -1.25, -9223372036854775808, 9223372036854775807,
                "add", "true", "null", "1", "é雪🙂", "line\nbreak", "", "a: b", "'quoted'", "\\"]
    for _ in range(30):
        value = {"value": [rng.choice(literals) for _ in range(rng.randrange(8))], "nested": {"text": rng.choice(literals)}}
        # Quote all strings so generated source cannot introduce symbols or expressions.
        source = json.dumps(value, ensure_ascii=False)
        for cycle in range(3):
            result = h.helix("run", "-", stdin=source)
            assert h.malformed_state(result.stdout, result.returncode) is None
            assert literal_meaning(yaml.safe_load(result.stdout)["root"]) == literal_meaning(value), f"seed={seed} cycle={cycle} source={source}"
            next_source = h.root_section(result.stdout)
            if cycle:
                assert next_source == source
            source = next_source


def dogfood(h):
    from tests.suites.dogfood import verify
    verify(h)


def imports(h):
    from tests.suites.dependencies import verify
    verify(h)
    with tempfile.TemporaryDirectory(dir=h.root / "runtime", prefix="imports-") as directory:
        base = Path(directory)
        (base / "shared.yaml").write_text("value: 21\n")
        for name in ("left", "right"):
            (base / f"{name}.yaml").write_text('{"s": {"$call": ["import", "shared.yaml"]}, "value": {"$call": ["add", {"$ref": "s.value"}, 0]}}')
        main = base / "main.yaml"
        main.write_text('{"l": {"$call": ["import", "left.yaml"]}, "r": {"$call": ["import", "right.yaml"]}, "answer": {"$call": ["add", {"$ref": "l.value"}, {"$ref": "r.value"}]}}')
        result = h.helix("run", str(main))
        assert result.returncode == 0 and yaml.safe_load(result.stdout)["root"]["answer"] == 42
        for content, kind in [("[", "parse"), ('{"value": {"$call": ["add", {"$ref": "missing"}, 1]}}', "unbound")]:
            (base / "shared.yaml").write_text(content)
            result = h.helix("run", str(main))
            assert h.malformed_state(result.stdout, result.returncode) is None
            error = yaml.safe_load(result.stdout)["error"]
            assert error["kind"] == kind and error["file"].endswith("shared.yaml"), error
            assert error["line"] == (2 if kind == "parse" else 1), error
        (base / "shared.yaml").write_text('{"again": {"$call": ["import", "main.yaml"]}}')
        result = h.helix("run", str(main))
        assert result.returncode == 1 and h.malformed_state(result.stdout, 1) is None
        (base / "shared.yaml").unlink()
        result = h.helix("run", str(main))
        assert result.returncode == 1 and h.malformed_state(result.stdout, 1) is None
        # A module cannot capture the importing file's lexical bindings.
        (base / "shared.yaml").write_text('{"value": {"$call": ["add", {"$ref": "private"}, 1]}}')
        main.write_text('{"private": 9, "s": {"$call": ["import", "shared.yaml"]}}')
        error = yaml.safe_load(h.helix("run", str(main)).stdout)["error"]
        assert error["kind"] == "unbound" and error["path"] == "/value/1", error


def run(name, h):
    try:
        if name in {"static", "interactive", "inspection"}:
            from importlib import import_module
            return import_module(f"tooling.checks_{name}").check(h)
        if name in globals():
            globals()[name](h)
        else:
            from importlib import import_module
            import_module(f"tests.suites.{name}").verify(h)
        print(f"{name}: passed (generated seed=714209)" if name == "generated" else f"{name}: passed")
        return []
    except Exception as error:
        import traceback
        return [f"{name}: {error}\n{traceback.format_exc()}\nreproduce: ./manage.py test --suite {name}"]


def parser(h):
    from tests.suites.parser_graph import verify
    verify(h)
    for source, expected in [('"é雪🙂"', "é雪🙂"), ("'a: b'", "a: b"),
                             ("|\n  first\n  second\n", "first\nsecond\n"),
                             (">\n  first\n  second\n", "first second\n"),
                             ('{"outer": {"n": 3, "result": {"$call": ["add", {"$ref": "n"}, 2]}}}', {"outer": {"n": 3, "result": 5}})]:
        assert checked(h, source) == expected
    duplicate = h.helix("run", "-", stdin="a: 1\na: 2\n")
    assert duplicate.returncode == 1 and h.malformed_state(duplicate.stdout, 1) is None
    for source in ["? [a, b]\n: value"]:
        result = h.helix("run", "-", stdin=source)
        assert h.malformed_state(result.stdout, result.returncode) is None
        assert yaml.safe_load(result.stdout)["error"]["kind"] == "unsupported"
    result = h.helix("run", "-", stdin='{"outer": {"result": {"$call": ["add", {"$ref": "missing"}, 1]}}}')
    error = yaml.safe_load(result.stdout)["error"]
    assert (error["path"], error["line"], error["column"]) == ("/outer/result/$call/1", 1, 40), error


def fuzz(h):
    seed = 901723
    rng = random.Random(seed)
    corpus = ['{"x": {"$call": ["add", 1, 2]}}', '{"f": {"$call": ["fn", ["x"], {"$call": ["add", {"$ref": "x"}, 1]}]}, "r": {"$call": ["f", 2]}}',
              '{"m": {"$call": ["import", "std:math"]}, "r": {"$call": ["m.square", 3]}}', '["add", "雪", null]',
              '{"r": {"$call": ["try", {"$call": ["div", 1, 0]}, {"$call": ["fn", ["e"], {"$ref": "e"}]}]}}']
    alphabet = '[]{}:,\n\t abcdef0129"\'&*!#雪'
    for sample in range(200):
        source = rng.choice(corpus)
        for _ in range(rng.randint(1, 5)):
            pos = rng.randrange(len(source) + 1)
            source = source[:pos] + rng.choice(alphabet) + source[pos + rng.randrange(2):]
        command = rng.choice(("run", "check"))
        def fails(candidate):
            result = h.helix(command, *(["--evaluate"] if command == "check" else []), "-", stdin=candidate)
            return h.malformed_state(result.stdout, result.returncode)
        why = fails(source)
        if why:
            # Bounded deletion minimization; preserve exactly the same failure category.
            minimized = source
            for _ in range(2):
                for pos in range(len(minimized) - 1, -1, -1):
                    candidate = minimized[:pos] + minimized[pos + 1:]
                    if fails(candidate) == why:
                        minimized = candidate
            artifact = h.root / "runtime" / f"fuzz-{seed}-{sample}.yaml"
            artifact.write_text(minimized)
            raise AssertionError(f"seed={seed} sample={sample}: {why}; original={source!r}; "
                                 f"artifact={artifact}; reproduce: ./manage.py {command} {artifact}")
    print(f"fuzz: seed={seed}, 200 mutations")


def scale(h):
    import time
    for count in (250, 1000, 2500):
        # One map root plus four nodes per expression = 10,001 nodes at the largest size.
        source = json.dumps({f"v{i}": {"$call": ["add", i, 1]} for i in range(count)})
        started = time.perf_counter()
        root = checked(h, source)
        elapsed = time.perf_counter() - started
        assert len(root) == count and root[f"v{count - 1}"] == count
        print(f"scale: {1 + 4 * count} nodes, {elapsed:.3f}s (debug CLI + Python decode)")
        assert elapsed < (1 if count == 2500 else 10), "scale workload exceeded its time budget"

def execution(h):
    import os
    import signal
    # Kill the whole Cargo/test process group on timeout, including a stuck test binary.
    with subprocess.Popen(["cargo", "test", "--quiet", "--test", "execution", "--test", "execution_memory", "--", "--nocapture"],
                          cwd=h.root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, start_new_session=True) as process:
        try:
            output, _ = process.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            output, _ = process.communicate()
            raise AssertionError(f"execution contracts timed out; {output}")
    (h.root / "runtime" / "execution-profile.log").write_text(output)
    print(output, end="")
    assert process.returncode == 0, "execution contracts failed"
