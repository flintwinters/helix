"""Deterministic safety boundaries; no platform memory oracle is needed here."""
import tempfile
from pathlib import Path

import yaml



def failure(h, source, kind, command="run"):
    result = h.helix(command, "-", stdin=source)
    assert h.malformed_state(result.stdout, result.returncode) is None, result
    state = yaml.safe_load(result.stdout)
    errors = state.get("errors", [state.get("error")])
    assert result.returncode == 1 and any(e["kind"] in ((kind,) if isinstance(kind, str) else kind) for e in errors), state
    return errors


def verify(h):
    # The last accepted syntax depth and first rejected depth; check takes a separate traversal.
    for command in ("run", "check"):
        accepted = h.helix(command, "-", stdin="[" * 64 + "0" + "]" * 64)
        assert accepted.returncode == 0, accepted
        failure(h, "[" * 65 + "0" + "]" * 65, "depth", command)
        failure(h, "[" * 4096 + "0" + "]" * 4096, ("depth", "parse"), command)
    # Exactly 100,000 graph nodes, then one more. Keep output decoding out of the workload.
    for count, code in ((99_999, 0), (100_000, 1)):
        result = h.helix("run", "-", stdin="[" + ",".join("0" for _ in range(count)) + "]")
        assert result.returncode == code and h.malformed_state(result.stdout, code) is None
        if code:
            assert yaml.safe_load(result.stdout)["error"]["kind"] == "limit"
    for n, code in ((198, 0), (200, 1)):
        source = f"f: [fn, [n], [if, [eq, n, 0], 0, [f, [sub, n, 1]]]]\nr: [f, {n}]"
        result = h.helix("run", "-", stdin=source)
        assert result.returncode == code, result
        if code:
            assert yaml.safe_load(result.stdout)["error"]["kind"] == "depth"
    for stop, code in ((99_999, 0), (100_000, 1)):
        source = f"[until, [fn, [n], [eq, n, {stop}]], [fn, [n], [add, n, 1]], 0]"
        result = h.helix("run", "-", stdin=source)
        assert result.returncode == code, result
        if code:
            assert yaml.safe_load(result.stdout)["error"]["kind"] == "limit"
    # Computed growth must fail before exhausting the host, even with shallow source.
    failure(h, '[until, [fn, [s], false], [fn, [s], [concat, s, s]], "x"]', "limit")
    failure(h, '[until, [fn, [s], false], [fn, [s], [list, s]], 0]', "depth")
    # Computed output must stay within the parser's graph budget to remain re-readable.
    failure(h, '[until, [fn, [xs], [ge, [len, xs], 131072]], [fn, [xs], [concat, xs, xs]], [list, 0]]', "limit")
    grown = h.helix("run", "-", stdin='[until, [fn, [xs], [ge, [len, xs], 65536]], [fn, [xs], [concat, xs, xs]], [list, 0]]')
    assert grown.returncode == 0, grown
    program = h.root_section(grown.stdout)
    reloaded = h.helix("run", "-", stdin=program)
    assert reloaded.returncode == 0 and h.root_section(reloaded.stdout) == program
    # Function and machine environments can retain growing graphs despite shallow source.
    for name, wrapper in [("function", "[fn, [], state]"), ("machine", "[vm, [fn, [], state]]")]:
        source = f'[until, [fn, [state], false], [fn, [state], {wrapper}], 0]'
        artifact = h.root / "runtime" / f"resource-{name}-capture.yaml"
        artifact.write_text(source + "\n")
        result = h.helix("run", str(artifact))
        why = h.malformed_state(result.stdout, result.returncode)
        assert why is None, f"{name} capture graph: {why}; replay: ./manage.py run {artifact}; stderr={result.stderr}"
        state = yaml.safe_load(result.stdout)
        assert result.returncode == 1 and state["error"]["kind"] in ("depth", "limit"), state
    # A long dependency chain has shallow syntax but a deep evaluator stack.
    source = "".join(f"v{i}: [add, v{i + 1}, 0]\n" for i in range(2000)) + "v2000: 0\n"
    failure(h, source, "depth")
    # Deep imports must stop during linking, including in an uncalled function.
    with tempfile.TemporaryDirectory(dir=h.root / "runtime", prefix="limits-") as directory:
        base = Path(directory)
        for i in range(70):
            (base / f"m{i}.yaml").write_text(f"next: [import, m{i + 1}.yaml]\n")
        (base / "m70.yaml").write_text("0\n")
        result = h.helix("run", str(base / "m0.yaml"))
        assert h.malformed_state(result.stdout, result.returncode) is None, result
        assert yaml.safe_load(result.stdout)["error"]["kind"] == "depth"
    print("resources: syntax 64/65; graph 100000/100001; calls; iterations 99999/100000; growth; dependencies; imports")
