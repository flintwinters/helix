"""Provider acceptance with independent host-state observations and effect traces."""
import json
from pathlib import Path
import sys
import tempfile

import yaml


def call(actor, *arguments):
    return {"$call": [actor, *arguments]}


def ref(name):
    return {"$ref": name}


def literal(value):
    return {"$literal": value}


def context(**changes):
    return {"platform": "test-linux", "capabilities": ["file_read", "file_write"],
            "managed": True, "allow_remove": False, **changes}


def descriptor(**changes):
    return {
        "version": 1, "id": "test.content", "revision": "1.0.0",
        "arguments": ref("string"), "observation": ref("string"), "result": call("literal", None),
        "platforms": ["test-linux"], "capabilities": ["file_read", "file_write"],
        "check": True, "diff": True, "failure": "stop",
        "observe": call("fn", ["args"], "old"),
        "plan": call("fn", ["args", "before"],
                     call("if", call("eq", ref("args"), ref("before")), [],
                          call("list", call("dict", "field", "/content", "op", "set", "to", ref("args"))))),
        "apply": call("fn", ["args", "steps"], None),
        **changes,
    }


def logged(event, value):
    return call("get", call("list", call("effect", "stdout", event + "\n"), value), 1)


def verify(h):
    base = h.root / "runtime" / "provider-contract"
    base.mkdir(parents=True, exist_ok=True)
    artifacts = Path(tempfile.mkdtemp(dir=base, prefix="run-"))
    print(f"provider_contract replay artifacts: {artifacts.relative_to(h.root)}")
    count = 0

    def execute(name, expression, spec=None, *, flags=(), perform=True):
        nonlocal count
        count += 1
        source = call("let", {"provider": call("import", "std:provider"),
                              "spec": descriptor() if spec is None else spec}, expression)
        path = artifacts / f"{name}.yaml"
        path.write_text(json.dumps(source, ensure_ascii=False) + "\n")
        process = h.helix("perform" if perform else "run", *flags, str(path))
        output = process.stderr if perform else process.stdout
        path.with_suffix(".state.yaml").write_text(output)
        assert h.malformed_state(output, process.returncode) is None, (name, process)
        state = yaml.safe_load(output)
        assert process.returncode == 0 and state["status"] == "finished", (name, state)
        report = state["root"]
        assert report["version"] == 1, (name, report)
        assert report["status"] in {"changed", "unchanged", "failed", "skipped", "unsupported"}, (name, report)
        return report, state, process.stdout.splitlines() if perform else []

    def run(name, spec=None, args="new", ctx=None, mode="check", flags=("--allow-stdout",),
            before="old", reviewed=None):
        inputs = [ref("spec"), literal(args), literal(context() if ctx is None else ctx)]
        if mode == "check":
            expression = call("provider.check", *inputs)
        else:
            review = literal(reviewed) if reviewed is not None else call(
                "provider.plan", inputs[0], inputs[1], literal(before), inputs[2])
            expression = call("provider.apply", *inputs, review)
        return execute(name, expression, spec, flags=flags)

    def plan(name, spec=None, args="new", before="old", ctx=None):
        return execute(name, call("type_time", call("provider.plan", ref("spec"), literal(args),
                       literal(before), literal(context() if ctx is None else ctx))), spec, perform=False)

    def failed(result, name):
        report = result[0]
        assert report["status"] == "failed", (name, report)
        assert isinstance(report.get("error"), dict), (name, report)
        assert {"kind", "message"} <= report["error"].keys(), (name, report)
        return report

    changed, state, _ = plan("pure_changed")
    assert changed["status"] == "changed" and changed["steps"] == [
        {"field": "/content", "op": "set", "to": "new"}], changed
    assert state.get("effects", []) == [], state
    unchanged, _, _ = plan("pure_unchanged", args="old")
    assert unchanged["status"] == "unchanged" and unchanged["steps"] == [], unchanged
    native = descriptor(observation=ref("map"), plan=call("fn", ["args", "before"],
        call("converge", call("realize", call("class", "managed-content", call("record", "content", ref("string")),
                              call("dict", "content", ref("args")))), ref("before"))))
    for name, before, steps in (
        ("converge_changed", {"content": "old", "unmanaged": "keep"},
         [{"field": "content", "op": "set", "from": "old", "to": "new"}]),
        ("converge_missing", {"unmanaged": "keep"}, [{"field": "content", "op": "set", "to": "new"}]),
        ("converge_unmanaged_preserved", {"content": "new", "unmanaged": "keep"}, []),
    ):
        report, _, _ = plan(name, native, before=before)
        assert report["steps"] == steps and report["status"] == ("changed" if steps else "unchanged"), report

    observable = descriptor(observe=call("fn", ["args"], logged("observe", "old")),
                            apply=call("fn", ["args", "steps"], logged("apply", None)))
    preview, state, events = run("preview", observable)
    assert preview["status"] == "changed" and preview["applied"] is False, preview
    assert events == ["observe"], events
    no_change, _, events = run("unchanged", observable, args="old", mode="apply")
    assert no_change["status"] == "unchanged" and events == ["observe"], (no_change, events)

    for name, spec, ctx, status in (
        ("unmanaged", observable, context(managed=False), "skipped"),
        ("unsupported_platform", observable, context(platform="other-os"), "unsupported"),
        ("missing_capability", observable, context(capabilities=["file_read"]), "unsupported"),
        ("preview_unsupported", {**observable, "check": False}, context(), "unsupported"),
    ):
        report, state, events = run(name, spec, ctx=ctx)
        assert report["status"] == status and events == [] and state.get("effects", []) == [], (name, report, state)
    report, _, events = run("no_diff_preview", {**observable, "diff": False})
    assert report["status"] == "unsupported" and events == [], (report, events)

    # Descriptor and argument errors must be caught before any observer executes.
    invalid_specs = [
        {**observable, "version": 2}, {**observable, "revision": ""},
        {**observable, "failure": "ignore"}, {**observable, "arguments": "unknown-type"},
        {**observable, "observation": False}, {**observable, "result": 1},
        {**observable, "result": {"type": "refine", "of": "string", "check": 1}},
        {**observable, "observe": False}, {**observable, "plan": None},
        {**observable, "apply": []}, {**observable, "check": "yes"},
        {**observable, "capabilities": [1]}, {**observable, "platforms": [False]},
    ]
    for index, spec in enumerate(invalid_specs):
        result = run(f"invalid_descriptor_{index:02}", spec)
        failed(result, index)
        assert result[2] == [] and result[1].get("effects", []) == [], result
    for name, kwargs in (
        ("argument_type", {"args": 42}),
        ("invalid_context", {"ctx": context(managed="yes")}),
    ):
        result = run(name, observable, **kwargs)
        failed(result, name)
        assert result[2] == [], result

    failed(run("observation_type", {**observable, "observe": call("fn", ["args"], 42)}), "observation_type")
    failed(plan("supplied_observation_type", before=42), "supplied_observation_type")
    for index, steps in enumerate((None, {}, [42], [{"field": "/content", "op": "erase", "to": None}],
                                   [{"field": "/content", "op": "set"}],
                                   [{"field": "content", "op": "set", "to": "a"},
                                    {"field": "content", "op": "set", "to": "b"}])):
        result = run(f"invalid_steps_{index}", {**observable, "plan": call("fn", ["args", "before"], literal(steps))}, mode="apply")
        failed(result, index)
        assert result[2] == ["observe"], result

    impure = {**observable, "plan": call("fn", ["args", "before"], logged("illegal-plan-effect", []))}
    result = run("plan_rejects_effects", impure, mode="apply")
    failed(result, "plan_rejects_effects")
    assert result[2] == ["observe"], result
    assert all(effect["payload"] != "illegal-plan-effect\n" for effect in result[1].get("effects", [])), result
    failed(plan("pure_plan_rejects_effects", impure), "pure_plan_rejects_effects")
    function_step = {**observable, "plan": call("fn", ["args", "before"],
        call("list", call("dict", "field", "content", "op", "set", "to", call("fn", [], None))))}
    failed(plan("executable_plan_value", function_step), "executable_plan_value")
    for field, primitive, events in (("arguments", ref("string"), []),
                                     ("observation", ref("string"), ["observe"]),
                                     ("result", call("literal", None), ["observe", "apply"])):
        impure_type = call("refine", primitive, call("fn", ["value"], logged("illegal-type-effect", True)))
        result = run(f"impure_{field}_refinement", {**observable, field: impure_type}, mode="apply")
        failed(result, field)
        assert result[2] == events, result
    function_value = call("fn", [], None)
    failed(execute("executable_arguments", call("provider.check", ref("spec"),
                   call("dict", "nested", function_value), literal(context())),
                   {**observable, "arguments": ref("any")}), "executable_arguments")
    failed(run("executable_observation", {**observable, "observation": ref("any"),
               "observe": call("fn", ["args"], call("dict", "nested", function_value))}), "executable_observation")
    dependency = artifacts / "file-input.yaml"
    dependency.write_text('"new"\n')
    result = execute("file_import_purity_boundary", call("provider.plan", ref("spec"),
                     call("import", str(dependency)), "old", literal(context())), perform=False)
    failed(result, "file_import_purity_boundary")
    assert result[1].get("effects", []) == [], result

    removal = {**observable, "plan": call("fn", ["args", "before"],
                                         [{"field": "/content", "op": "remove", "to": None}])}
    result = run("deletion_requires_opt_in", removal, mode="apply")
    assert result[0]["status"] in {"failed", "unsupported"} and "apply" not in result[2], result
    allowed, _, _ = plan("deletion_explicit", removal, ctx=context(allow_remove=True))
    assert allowed["status"] == "changed" and allowed["steps"][0]["op"] == "remove", allowed
    result = run("unmanaged_deletion", removal, ctx=context(managed=False, allow_remove=True), mode="apply")
    assert result[0]["status"] == "skipped" and result[2] == [], result

    # A callback claiming success without changing reality must fail verification.
    result = run("successful_callback_unmet_postcondition", observable, mode="apply")
    failed(result, "successful_callback_unmet_postcondition")
    assert result[2] == ["observe", "apply", "observe"], result
    throwing = {**observable, "apply": call("fn", ["args", "steps"], call("raise", "mutation_failed", "write rejected"))}
    result = run("apply_exception", throwing, mode="apply")
    assert failed(result, "apply_exception")["error"]["kind"] == "mutation_failed", result
    assert result[2] == ["observe"], result
    assert result[0]["attempted"] is True and result[0]["applied"] is False and result[0]["phase"] == "apply", result
    wrong_result = {**observable, "apply": call("fn", ["args", "steps"], logged("apply", 42))}
    failed(run("apply_result_type", wrong_result, mode="apply"), "apply_result_type")

    # Actual authorized reads/writes, checked independently in Python. A sibling
    # file represents unmanaged state that every lifecycle path must preserve.
    managed = artifacts / "managed.txt"
    unmanaged = artifacts / "unmanaged.txt"
    managed.write_text("old")
    unmanaged.write_text("keep me\n")
    file_spec = descriptor(
        observe=call("fn", ["args"], call("effect", "file_read", str(managed))),
        apply=call("fn", ["args", "steps"], call("effect", "file_write",
                    call("dict", "path", str(managed), "text", ref("args")))))
    grants = ("--allow-read", str(artifacts), "--allow-write", str(artifacts))
    report, state, _ = run("file_preview", file_spec, flags=grants)
    assert report["status"] == "changed" and managed.read_text() == "old", report
    assert [item["operation"] for item in state["effects"]] == ["file_read"], state
    reviewed = report
    for name, kwargs in (
        ("review_argument_mismatch", {"args": "different"}),
        ("review_revision_mismatch", {"spec": {**file_spec, "revision": "2.0.0"}}),
        ("review_context_mismatch", {"ctx": context(allow_remove=True)}),
    ):
        options = {"spec": file_spec, "mode": "apply", "flags": grants, "reviewed": reviewed, **kwargs}
        result = run(name, **options)
        assert failed(result, name)["error"]["kind"] == "provider_stale_plan", result
        assert managed.read_text() == "old", result
        assert all(item["operation"] != "file_write" for item in result[1].get("effects", [])), result
    managed.write_text("concurrent change")
    result = run("stale_observation", file_spec, mode="apply", flags=grants, reviewed=reviewed)
    assert failed(result, "stale_observation")["error"]["kind"] == "provider_stale_plan", result
    assert managed.read_text() == "concurrent change", result
    managed.write_text("old")
    report, state, _ = run("file_apply", file_spec, mode="apply", flags=grants, reviewed=reviewed)
    assert report["status"] == "changed" and report["applied"] is True and managed.read_text() == "new", report
    assert [item["operation"] for item in state["effects"]] == ["file_read", "file_write", "file_read"], state
    report, state, _ = run("file_second_noop", file_spec, mode="apply", flags=grants, before="new")
    assert report["status"] == "unchanged" and len(state["effects"]) == 1, (report, state)
    managed.write_text("drift")
    report, _, _ = run("file_drift_repair", file_spec, mode="apply", flags=grants, before="drift")
    assert report["status"] == "changed" and managed.read_text() == "new", report
    report, _, _ = run("file_unmanaged", file_spec, args="must not write", ctx=context(managed=False), mode="apply", flags=grants)
    assert report["status"] == "skipped" and managed.read_text() == "new", report
    result = run("advertised_capability_is_not_authority", file_spec, args="denied", mode="apply",
                 flags=("--allow-read", str(artifacts)), before="new")
    failed(result, "advertised_capability_is_not_authority")
    assert managed.read_text() == "new" and unmanaged.read_text() == "keep me\n"
    result = run("missing_read_grant", file_spec, flags=())
    failed(result, "missing_read_grant")
    assert managed.read_text() == "new", result
    wrote_then_failed = {**file_spec, "apply": call("fn", ["args", "steps"],
        call("list", call("effect", "file_write", {"path": str(managed), "text": "partial"}),
             call("raise", "partial_mutation", "failed after write")))}
    result = run("mutation_before_exception", wrote_then_failed, before="new", args="wanted", mode="apply", flags=grants)
    report = failed(result, "mutation_before_exception")
    assert report["attempted"] is True and report["applied"] is False and report["phase"] == "apply", report
    assert managed.read_text() == "partial", report
    observe_after_failure = {**file_spec, "observe": call("fn", ["args"], call("let",
        {"text": call("effect", "file_read", str(managed))}, call("if", call("eq", ref("text"), "wanted"),
            call("raise", "observation_lost", "post-mutation observer failed"), ref("text"))))}
    result = run("post_mutation_observation_failure", observe_after_failure, before="partial", args="wanted", mode="apply", flags=grants)
    report = failed(result, "post_mutation_observation_failure")
    assert report["applied"] is True and report["after"]["status"] == "failed", report
    assert managed.read_text() == "wanted", report
    managed.unlink()
    result = run("inaccessible_is_not_absent", file_spec, mode="apply", flags=grants)
    failed(result, "inaccessible_is_not_absent")
    assert not managed.exists() and unmanaged.read_text() == "keep me\n"
    # Exercise explicit removal against a real file. The observation helper only
    # maps FileNotFoundError to absence; permission/transport/process failures
    # remain errors. Executable authority is supplied by this test's caller.
    removable = artifacts / "remove-me.txt"
    removable.write_text("owned")
    observe_code = ("import os, sys\ntry:\n os.stat(sys.argv[1])\n"
                    "except FileNotFoundError:\n print('absent')\nelse:\n print('present')\n")
    remove_code = "import os, sys\nos.unlink(sys.argv[1])\n"

    def command(script):
        return call("effect", "command", {"program": sys.executable,
                    "args": ["-c", script, str(removable)]})

    def checked_command(script, answer):
        return call("call", call("fn", ["reply"], call("if",
                    call("eq", call("get", ref("reply"), "code"), 0), answer,
                    call("raise", "file_adapter_failure", "File operation failed", ref("reply")))), command(script))

    removal_spec = descriptor(
        arguments=ref("boolean"), observation=ref("boolean"), capabilities=["command"],
        observe=call("fn", ["args"], checked_command(observe_code,
                     call("eq", call("get", ref("reply"), "stdout"), "present\n"))),
        plan=call("fn", ["args", "before"], call("if", call("eq", ref("args"), ref("before")), [],
                    [{"field": "presence", "op": "remove", "to": None}])),
        apply=call("fn", ["args", "steps"], checked_command(remove_code, None)))
    removal_grants = ("--allow-command", sys.executable)
    removal_context = context(capabilities=["command"])
    result = run("file_removal_denied", removal_spec, args=False, before=True,
                 ctx=removal_context, mode="apply", flags=removal_grants)
    assert failed(result, "file_removal_denied")["error"]["kind"] == "provider_removal_denied", result
    assert removable.read_text() == "owned" and unmanaged.read_text() == "keep me\n", result
    removal_context["allow_remove"] = True
    review, _, _ = run("file_removal_review", removal_spec, args=False,
                      ctx=removal_context, flags=removal_grants)
    assert review["status"] == "changed" and removable.exists(), review
    report, state, _ = run("file_removal_apply", removal_spec, args=False, ctx=removal_context,
                          reviewed=review, mode="apply", flags=removal_grants)
    assert report["status"] == "changed" and report["applied"] is True and not removable.exists(), report
    assert [item["operation"] for item in state["effects"]] == ["command"] * 3, state
    report, state, _ = run("file_removal_second_noop", removal_spec, args=False, before=False,
                          ctx=removal_context, mode="apply", flags=removal_grants)
    assert report["status"] == "unchanged" and len(state["effects"]) == 1 and not removable.exists(), report
    assert unmanaged.read_text() == "keep me\n", report
    example = h.helix("run", str(h.root / "tests" / "programs" / "provider_contract.yaml"))
    assert example.returncode == 0 and h.malformed_state(example.stdout, 0) is None, example
    report = yaml.safe_load(example.stdout)["root"]
    assert report["status"] == "changed" and report["steps"] == [
        {"field": "content", "op": "set", "from": "observed text", "to": "desired text"}], report
    print(f"provider_contract: {count} lifecycle scenarios; independent file state and effect-trace assertions")
