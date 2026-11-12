"""Step, trace and saved-state observation contracts; run via manage.py test."""
import yaml


def check(ctx):
    source = (ctx.root / "tests/cases/daily_inspection.yaml").read_text()
    initial = ctx.helix("step", "-", "--steps", "0", stdin=source)
    assert initial.returncode == 0 and not initial.stderr, initial
    ready = yaml.safe_load(initial.stdout)
    assert ready["status"] == "ready" and ready["transition"] == 0
    assert ready["restorable"] is False and ready["pending_effect"] is None
    stepped = ctx.helix("step", "-", stdin=source)
    active = yaml.safe_load(stepped.stdout)
    assert stepped.returncode == 0 and active["status"] == "running", stepped
    assert active["transition"] == 1 and active["location"] is not None
    trace = ctx.helix("trace", "-", stdin=source)
    assert trace.returncode == 0 and not trace.stderr, trace
    versions = list(yaml.safe_load_all(trace.stdout))
    assert versions[0] == ready and versions[1] == active
    assert [view["transition"] for view in versions] == list(range(len(versions)))
    assert versions[-1]["status"] == "finished"
    normal = ctx.helix("run", "-", stdin=source)
    assert versions[-1]["state"] == yaml.safe_load(normal.stdout)
    assert len({str(view["location"]) for view in versions}) > 2
    limited = ctx.helix("trace", "-", "--limit", "2", stdin=source)
    assert limited.returncode == 0
    assert list(yaml.safe_load_all(limited.stdout)) == versions[:3]
    final = ctx.helix("step", "-", "--steps", "10000", stdin=source)
    assert yaml.safe_load(final.stdout) == versions[-1]
    for saved, status, kind in [(normal.stdout, "finished", "map"),
                                 (stepped.stdout, "running", "absent")]:
        result = ctx.helix("inspect", "-", stdin=saved)
        summary = yaml.safe_load(result.stdout)
        assert result.returncode == 0 and not result.stderr, result
        assert summary["status"] == status and summary["root"]["kind"] == kind
        assert summary["pending_effect"] is None
        if status == "running":
            assert summary["location"] == active["location"] and summary["continuations"] > 0
    # Saved lists resembling expressions and missing imports must remain inert data.
    inert = 'status: finished\nroot: [import, "does-not-exist.yaml"]\n'
    result = ctx.helix("inspect", "-", stdin=inert)
    assert result.returncode == 0 and yaml.safe_load(result.stdout)["root"] == {"kind": "list", "size": 2}
    failed = ctx.helix("trace", "-", stdin="answer: [div, 1, 0]")
    assert failed.returncode == 1
    failure = list(yaml.safe_load_all(failed.stdout))[-1]
    assert failure["state"]["error"]["kind"] == "divide_by_zero"
    inspected = ctx.helix("inspect", "-", stdin=yaml.safe_dump(failure["state"]))
    assert inspected.returncode == 0
    assert yaml.safe_load(inspected.stdout)["error"] == failure["state"]["error"]
    pending = {"status": "signaled", "pending_effect": {"actor": "read_file", "arguments": ["example"]}}
    observed = ctx.helix("inspect", "-", stdin=yaml.safe_dump(pending))
    assert observed.returncode == 0
    assert yaml.safe_load(observed.stdout)["pending_effect"] == pending["pending_effect"]
    # A single-document inspector rejects streams, duplicate keys, arbitrary YAML and corrupt status.
    for bad in ("[", "42", "status: other", "status: ready\nstatus: error", "status: ready\n---\nstatus: finished"):
        assert ctx.helix("inspect", "-", stdin=bad).returncode == 2, bad
    for command in ("step", "trace", "inspect"):
        assert ctx.helix(command, "runtime/no-such-inspection-file.yaml").returncode == 2
    malformed = ctx.helix("step", "-", stdin="[")
    assert malformed.returncode == 1 and yaml.safe_load(malformed.stdout)["status"] == "error"
    return []
