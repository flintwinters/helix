"""Guarded command resources checked against independent local process/file state."""
import json
from pathlib import Path
import sys
import tempfile

import yaml

from tests.suites.provider_contract import call, ref, literal


def verify(h):
    base = h.root / "runtime" / "command-provider"
    base.mkdir(parents=True, exist_ok=True)
    artifacts = Path(tempfile.mkdtemp(dir=base, prefix="run-"))
    print(f"command_provider replay artifacts: {artifacts.relative_to(h.root)}")
    state_file = artifacts / "state.txt"
    log_file = artifacts / "executions.jsonl"
    sentinel = artifacts / "unmanaged.txt"
    sentinel.write_text("preserve\n")
    special = "spaces ; $(touch unintended) ' quotes\nUnicode: λ"
    script = (
        "import json,pathlib,sys\n"
        "with pathlib.Path(sys.argv[2]).open('a') as log: log.write(json.dumps(sys.argv[3:])+'\\n')\n"
        "pathlib.Path(sys.argv[1]).write_text('done')\n"
        "print('changed')\n"
    )
    args = {"program": sys.executable, "args": ["-c", script, str(state_file), str(log_file), special]}
    ctx = {"platform": "test-linux", "capabilities": ["command", "file_read"],
           "managed": True, "allow_remove": False}
    config = {
        "id": "test.guarded-command", "revision": "1.0.0", "platforms": ["test-linux"],
        "capabilities": ["command", "file_read"], "observation": ref("string"),
        "observe": call("fn", ["args"], call("effect", "file_read", str(state_file))),
        "guard": call("fn", ["args", "before"], call("ne", ref("before"), "done")),
        "changed": call("fn", ["args", "reply"], call("eq", call("get", ref("reply"), "stdout"), "changed\n")),
        "failed": call("fn", ["args", "reply"], call("ne", call("get", ref("reply"), "code"), 0)),
    }
    grants = ("--allow-read", str(artifacts), "--allow-command", sys.executable)
    count = 0

    def reset(value="pending"):
        state_file.write_text(value)
        log_file.unlink(missing_ok=True)

    def execute(name, *, mode="check", cfg=None, argv=None, context=None, before="pending", review=None, flags=grants,
                construction_error=False):
        nonlocal count
        count += 1
        inputs = [ref("spec"), literal(args if argv is None else argv), literal(ctx if context is None else context)]
        if mode == "plan":
            expression = call("command.plan", inputs[0], inputs[1], literal(before), inputs[2])
        elif mode == "apply":
            reviewed = literal(review) if review is not None else call("command.plan", inputs[0], inputs[1], literal(before), inputs[2])
            expression = call("command.apply", *inputs, reviewed)
        else:
            expression = call("command.check", *inputs)
        source = call("let", {"command": call("import", "std:command"),
                              "spec": call("command.make", config if cfg is None else cfg)}, expression)
        path = artifacts / f"{name}.yaml"
        path.write_text(json.dumps(source, ensure_ascii=False) + "\n")
        proc = h.helix("perform", *flags, str(path))
        path.with_suffix(".state.yaml").write_text(proc.stderr)
        assert h.malformed_state(proc.stderr, proc.returncode) is None, (name, proc)
        state = yaml.safe_load(proc.stderr)
        if construction_error:
            assert proc.returncode == 1 and state["status"] == "error", (name, state)
            assert state.get("effects", []) == [] and executions() == [], (name, state)
            return state["error"], []
        assert proc.returncode == 0 and state["status"] == "finished", (name, state)
        assert sentinel.read_text() == "preserve\n", name
        return state["root"], state.get("effects", [])

    def failed(result, kind=None):
        report = result[0]
        assert report["status"] == "failed", report
        if kind:
            assert report["error"]["kind"] == kind, report
        return report

    def executions():
        return [json.loads(line) for line in log_file.read_text().splitlines()] if log_file.exists() else []

    reset()
    report, effects = execute("pure_plan", mode="plan")
    assert report["status"] == "changed" and report["steps"] and effects == [], (report, effects)
    assert state_file.read_text() == "pending" and executions() == []
    review, effects = execute("preview")
    assert review == report and [item["operation"] for item in effects] == ["file_read"]
    assert state_file.read_text() == "pending" and executions() == []
    report, effects = execute("apply", mode="apply", review=review)
    assert report["status"] == "changed" and report["applied"] is True, report
    assert state_file.read_text() == "done" and executions() == [[special]], report
    assert [item["operation"] for item in effects] == ["file_read", "command", "file_read"], effects
    assert report["result"]["changed"] is True and report["result"]["reply"]["code"] == 0, report
    report, effects = execute("second_noop", mode="apply", before="done")
    assert report["status"] == "unchanged" and executions() == [[special]], report
    assert [item["operation"] for item in effects] == ["file_read"], effects
    state_file.write_text("drift")
    report, _ = execute("drift_repair", mode="apply", before="drift")
    assert report["status"] == "changed" and executions() == [[special], [special]] and state_file.read_text() == "done"

    reset()
    review, _ = execute("stale_review")
    state_file.write_text("different pending state")
    failed(execute("stale_observation", mode="apply", review=review), "provider_stale_plan")
    assert executions() == [] and state_file.read_text() == "different pending state"
    state_file.write_text("pending")
    for name, changes in (
        ("stale_arguments", {"argv": {**args, "args": args["args"] + ["new"]}}),
        ("stale_revision", {"cfg": {**config, "revision": "2.0.0"}}),
    ):
        failed(execute(name, mode="apply", review=review, **changes), "provider_stale_plan")
        assert executions() == []
    report, effects = execute("unmanaged", mode="apply", context={**ctx, "managed": False})
    assert report["status"] == "skipped" and effects == [] and executions() == [], report
    report, effects = execute("missing_metadata_capability", context={**ctx, "capabilities": ["file_read"]})
    assert report["status"] == "unsupported" and effects == []
    failed(execute("metadata_does_not_grant_authority", mode="apply", flags=("--allow-read", str(artifacts))))
    assert executions() == [] and state_file.read_text() == "pending"
    failed(execute("missing_observation_authority", flags=()))
    state_file.unlink()
    failed(execute("missing_observation_not_absence"))
    assert executions() == []

    reset()
    quiet = {**config, "changed": call("fn", ["args", "reply"], False)}
    report, _ = execute("explicit_unchanged_result", mode="apply", cfg=quiet)
    assert report["status"] == "unchanged" and report["applied"] is True, report
    assert report["result"]["changed"] is False and executions() == [[special]] and state_file.read_text() == "done"
    reset()
    reject = {**config, "failed": call("fn", ["args", "reply"], True),
              "changed": call("fn", ["args", "reply"], call("raise", "changed_must_not_run", "failure takes priority"))}
    report = failed(execute("zero_exit_rejected", mode="apply", cfg=reject), "command_failed")
    assert report["attempted"] is True and executions() == [[special]] and state_file.read_text() == "done"
    reset()
    accepted = {**config, "failed": call("fn", ["args", "reply"], False)}
    report, _ = execute("nonzero_exit_accepted", mode="apply", cfg=accepted,
                        argv={**args, "args": ["-c", script + "sys.exit(7)\n", *args["args"][2:]]})
    assert report["status"] == "changed" and report["result"]["reply"]["code"] == 7 and executions() == [[special]], report
    reset()
    no_mutation = {"program": sys.executable, "args": ["-c", "print('changed')"]}
    report = failed(execute("successful_exit_unmet_postcondition", mode="apply", argv=no_mutation), "provider_postcondition")
    assert report["applied"] is True and state_file.read_text() == "pending" and executions() == []
    report = failed(execute("unchanged_result_cannot_bypass_postcondition", mode="apply", argv=no_mutation,
                            cfg={**quiet, "guard": call("fn", ["args", "before"], True)}), "provider_postcondition")
    assert report["applied"] is True and report["result"]["changed"] is False and executions() == [], report

    for field, parameters in (("guard", ["args", "before"]), ("changed", ["args", "reply"]), ("failed", ["args", "reply"])):
        for variant, body in (("wrong_type", "not boolean"),
                              ("impure", call("effect", "file_write", {"path": str(sentinel), "text": "leaked"}))):
            reset()
            result = execute(f"{field}_{variant}", mode="apply", cfg={**config, field: call("fn", parameters, body)},
                             flags=(*grants, "--allow-write", str(artifacts)))
            failed(result)
            assert all(effect["operation"] != "file_write" for effect in result[1]), result
            assert executions() == ([] if field == "guard" else [[special]]), result
    reset()
    for name, bad_args in (("invalid_program", {**args, "program": 1}),
                           ("invalid_argv", {**args, "args": [1]})):
        report, effects = execute(name, mode="apply", argv=bad_args)
        assert report["status"] == "failed" and effects == [] and executions() == [], report
    for field in ("guard", "changed", "failed"):
        execute(f"required_{field}", cfg={key: value for key, value in config.items() if key != field},
                construction_error=True)
    execute("declared_command_capability_required", cfg={**config, "capabilities": ["file_read"]},
            construction_error=True)

    example = h.helix("run", str(h.root / "tests" / "programs" / "command_provider.yaml"))
    assert example.returncode == 0 and h.malformed_state(example.stdout, 0) is None, example
    report = yaml.safe_load(example.stdout)["root"]
    assert report["status"] == "changed" and report["applied"] is False and report["steps"], report
    print(f"command_provider: {count} scenarios; independent subprocess, argument and file-state assertions")
