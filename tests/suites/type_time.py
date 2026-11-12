"""Contracts for explicit pure type regions, exercised through the public CLI."""
import yaml


def state(h, command, source):
    result = h.helix(command, "-", stdin=source)
    assert h.malformed_state(result.stdout, result.returncode) is None, result
    return yaml.safe_load(result.stdout)


def verify(h):
    cases = [h.CASES / f"{name}.yaml" for name in (
        "types_reflection", "types_type_time", "types_type_time_effect", "types_type_time_error")]
    for case in cases:
        result = h.helix("run", str(case))
        assert result.stdout == case.with_suffix(".expected.yaml").read_text(), case
        root_source = h.root_section(result.stdout)
        if root_source is not None:
            rerun = h.helix("run", "-", stdin=root_source)
            assert h.root_section(rerun.stdout) == root_source, case
    for actor in ("type_time", "open_fields"):
        for source in (f"[{actor}]", f"[{actor}, null, null]"):
            assert state(h, "run", source)["error"]["kind"] == "arity"
    failures = h.robustness(cases)
    assert not failures, failures[:3]
    # Both entrypoints compute identical types and phase-local error values.
    source = (h.CASES / "types_type_time.yaml").read_text()
    run = state(h, "run", source)
    check = state(h, "check", source)
    assert run["root"] == check["root"], (run, check)
    root = run["root"]
    assert root["caught_inside"]["phase"] == root["caught_outside"]["phase"] == "type"
    assert root["rechecked"]["phase"] == "type"
    assert "phase" not in root["runtime_after"] and "phase" not in root["captured_error"]
    assert root["derived"]["fields"]["name"] == {"type": "optional", "of": "string"}
    # An inner phase cannot clear an outer phase, including after a caught failure.
    nested = state(h, "run", '[type_time, [finally, [type_time, integer], [div, 1, 0]]]')
    assert nested["phase"] == nested["error"]["phase"] == "type"
    # Limits are those of the ordinary evaluator, for both CLI entrypoints.
    for command in ("run", "check"):
        for source, kind in [
            ('[type_time, [until, [fn, [n], false], [fn, [n], n], 0]]', "limit"),
            ('f: [fn, [n], [f, n]]\nr: [type_time, [f, 0]]', "depth"),
        ]:
            result = state(h, command, source)
            errors = result.get("errors", [result.get("error")])
            assert any(e["kind"] == kind and e.get("phase") == "type" for e in errors), result
    # File dependencies cannot be admitted by laziness, memoization or a constant result.
    source = (h.CASES / "types_type_time_effect.yaml").read_text().replace("modules/geometry.yaml", "tests/cases/modules/geometry.yaml")
    for command in ("run", "check"):
        result = state(h, command, source)
        error = result["root"]["blocked"]
        assert error["kind"] == "effect" and error["phase"] == "type", error
        assert error["path"] == "/module" and result["root"]["runtime_after"] == 42
    print("type_time: run/check parity, nested phases, caught errors, file isolation and bounded evaluation")
