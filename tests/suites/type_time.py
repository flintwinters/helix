"""Contracts for explicit pure type regions, exercised through the public CLI."""
import json
from pathlib import Path
import tempfile

import yaml


def state(h, command, source):
    result = h.helix(command, *(["--evaluate"] if command == "check" else []), "-", stdin=source)
    assert h.malformed_state(result.stdout, result.returncode) is None, result
    return yaml.safe_load(result.stdout)


def verify(h):
    verify_check_mode(h)
    cases = [h.root / "tests" / "cases" / f"{name}.yaml" for name in (
        "types_reflection", "types_type_time", "types_type_time_effect", "types_type_time_error")]
    for case in cases:
        result = h.helix("run", str(case.relative_to(h.root)))
        assert result.stdout == case.with_suffix(".expected.yaml").read_text(), case
        root_source = h.root_section(result.stdout)
        if root_source is not None:
            rerun = h.helix("run", "-", stdin=root_source)
            assert h.root_section(rerun.stdout) == root_source, case
    for actor in ("type_time", "open_fields"):
        for source in (f'{{"$call": ["{actor}"]}}', f'{{"$call": ["{actor}", null, null]}}'):
            assert state(h, "run", source)["error"]["kind"] == "arity"
    failures = h.robustness(cases)
    assert not failures, failures[:3]
    # Both entrypoints compute identical types and phase-local error values.
    source = (h.root / "tests" / "cases" / "types_type_time.yaml").read_text()
    run = state(h, "run", source)
    check = state(h, "check", source)
    assert run["root"] == check["root"], (run, check)
    assert check["mode"] == "check" and "phase" not in check
    root = run["root"]
    assert root["caught_inside"]["phase"] == root["caught_outside"]["phase"] == "type"
    assert root["rechecked"]["phase"] == "type"
    assert "phase" not in root["runtime_after"] and "phase" not in root["captured_error"]
    assert root["derived"]["fields"]["name"] == {"type": "optional", "of": "string"}
    # An inner phase cannot clear an outer phase, including after a caught failure.
    nested = state(h, "run", '{"$call": ["type_time", {"$call": ["finally", {"$call": ["type_time", {"$ref": "integer"}]}, {"$call": ["div", 1, 0]}]}]}')
    assert nested["phase"] == nested["error"]["phase"] == "type"
    # Limits are those of the ordinary evaluator, for both CLI entrypoints.
    for command in ("run", "check"):
        for source, kind in [
            ('{"$call": ["type_time", {"$call": ["until", {"$call": ["fn", ["n"], false]}, {"$call": ["fn", ["n"], {"$ref": "n"}]}, 0]}]}', "limit"),
            ('{"f": {"$call": ["fn", ["n"], {"$call": ["f", {"$ref": "n"}]}]}, "r": {"$call": ["type_time", {"$call": ["f", 0]}]}}', "depth"),
        ]:
            result = state(h, command, source)
            errors = result.get("errors", [result.get("error")])
            assert any(e["kind"] == kind and e.get("phase") == "type" for e in errors), result
    # Loading source is separate from executing effects in a pure region.
    source = (h.root / "tests" / "cases" / "types_type_time_effect.yaml").read_text().replace('"modules/', '"tests/cases/modules/')
    for command in ("run", "check"):
        result = state(h, command, source)
        error = result["root"]["blocked"]
        assert error["kind"] == "purity" and error["phase"] == "type", error
        assert error["file"].endswith("modules/phase_effect.yaml"), error
        assert result["root"]["area"] == 12 and result["root"]["runtime_after"] == 42
    verify_linked_phases(h)
    print("type_time: run/check parity, nested phases, linked source, effect isolation and bounded evaluation")


def verify_linked_phases(h):
    def call(actor, *args):
        return {"$call": [actor, *args]}

    def ref(name):
        return {"$ref": name}

    catch = call("fn", ["error"], ref("error"))
    with tempfile.TemporaryDirectory(dir=h.root / "runtime", prefix="phase-imports-") as directory:
        base = Path(directory)
        module = base / "module.yaml"
        leaf = base / "leaf.yaml"
        main = base / "main.yaml"
        leaf.write_text("value: 21\n")
        module.write_text(json.dumps({"leaf": call("import", "leaf.yaml"),
                                      "twice": call("fn", [], call("mul", ref("leaf.value"), 2))}))
        source = {"module": call("import", "module.yaml"),
                  "answer": call("type_time", call("module.twice")),
                  "direct": call("type_time", call("get", call("import", "leaf.yaml"), "value")),
                  "independent": call("type_time", 7)}
        main.write_text(json.dumps(source))
        for command in ("run", "check"):
            process = h.helix(command, *(["--evaluate"] if command == "check" else []), str(main))
            result = yaml.safe_load(process.stdout)
            assert process.returncode == 0 and result["root"]["answer"] == 42, result
            assert result["root"]["direct"] == 21 and result["root"]["independent"] == 7, result

        # Pure evaluation must not bypass the linker's caller-supplied import roots.
        process = h.helix("sandbox", "-", str(main))
        error = yaml.safe_load(process.stdout)["error"]
        assert process.returncode == 1 and error["kind"] == "permission", error
        process = h.helix("sandbox", str(base), str(main))
        assert process.returncode == 0 and yaml.safe_load(process.stdout)["root"]["answer"] == 42

        # Evaluate an effectful imported field first, then read it in a pure region.
        # try keeps this region in ordinary order, so the runtime memo really exists.
        module.write_text(json.dumps({"value": call("effect", "stdout", "once\n")}))
        main.write_text(json.dumps(call("let", {"module": call("import", "module.yaml")},
            call("list", ref("module.value"),
                 call("try", call("type_time", ref("module.value")), catch)))))
        process = h.helix("perform", "--allow-read", str(base), "--allow-stdout", str(main))
        result = yaml.safe_load(process.stderr)
        assert process.returncode == 0 and process.stdout == "once\n", (process, result)
        error = result["root"][1]
        assert error["kind"] == "purity" and error["phase"] == "type", result
        assert error["file"].endswith("module.yaml") and error["path"] == "/value", error
        assert len(result.get("effects", [])) == 1, result


def verify_check_mode(h):
    # Collection mode admits file inputs and leaves uncalled bodies/unchosen branches alone.
    source = ('{"module": {"$call": ["import", "tests/cases/modules/geometry.yaml"]}, "answer": {"$call": ["module.area", 2]}, "unused": {"$call": ["fn", [], {"$call": ["div", 1, 0]}]}, "chosen": {"$call": ["if", true, 42, {"$call": ["div", 1, 0]}]}}')
    run = state(h, "run", source)
    checked = state(h, "check", source)
    assert checked["root"] == run["root"] and checked["root"]["answer"] == 12
    assert checked["mode"] == "check" and "phase" not in checked
    assert "mode" not in run and "phase" not in run
    # Loading failures are not type-time failures either.
    for source in ('[', '{"$call": ["import", "std:no_such_module"]}'):
        checked = state(h, "check", source)
        assert checked["mode"] == "check" and "phase" not in checked
        assert "phase" not in checked["errors"][0]
    # The same dependency can fail in both phases; deduplicate only within each phase.
    result = h.helix("check", "--evaluate", "tests/check/mixed_phases.yaml")
    assert h.malformed_state(result.stdout, result.returncode) is None, result
    checked = yaml.safe_load(result.stdout)
    assert checked["mode"] == "check" and "phase" not in checked
    errors = checked["errors"]
    assert len(errors) == 2 and [e.get("phase") for e in errors] == [None, "type"], errors
    assert all(e["kind"] == "divide_by_zero" and e["path"] == "/failure" for e in errors)
