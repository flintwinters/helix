"""Pure failed-run recovery contracts, invoked by the CLI suite."""
import yaml
from tests.suites.source_contracts import location


def verify(h):
    source = (h.root / "tests" / "cases" / "error_recovery.yaml").read_text()
    failed = h.helix("recover", "-", stdin=source)
    assert failed.returncode == 1 and not failed.stderr
    for command, original in [("recover", source), ("resume", failed.stdout)]:
        for damaged in h.mutations(original):
            result = h.helix(command, "-", stdin=damaged)
            assert h.malformed_state(result.stdout, result.returncode) is None, (command, damaged, result)
    state = yaml.safe_load(failed.stdout)
    assert state["error"]["kind"] == "divide_by_zero"
    recovery = state["recovery"]
    # Edit through YAML rather than assuming a particular scalar layout.
    program = yaml.safe_load(recovery)
    assert program["done"] == {"$literal": 42}, program
    assert program["broken"]["$call"][0] == "div" and "add" not in recovery
    program["broken"]["$call"][-1] = 2
    state["recovery"] = yaml.safe_dump(program, sort_keys=False)
    resumed = h.helix("resume", "-", stdin=yaml.safe_dump(state))
    assert resumed.returncode == 0, resumed.stdout
    assert yaml.safe_load(resumed.stdout)["root"] == {"done": 42, "broken": 3}
    # Repeated failure retains the successfully computed field and remains retryable.
    retry = h.helix("resume", "-", stdin=failed.stdout)
    assert retry.returncode == 1
    assert yaml.safe_load(retry.stdout)["recovery"] == recovery
    for invalid in ["[", "null", "status: finished\nrecovery: '42'", "status: error\nrecovery: 42"]:
        result = h.helix("resume", "-", stdin=invalid)
        assert result.returncode == 1 and yaml.safe_load(result.stdout)["error"]["kind"] == "recovery", (invalid, result)
    # Source literals and functions keep their meaning after a fresh process reload.
    source = '{"text": "add", "items": ["add", "x"], "f": {"$call": ["fn", ["x"], {"$call": ["add", {"$ref": "x"}, 1]}]}, "bad": {"$call": ["div", 1, 0]}}'
    state = yaml.safe_load(h.helix("recover", "-", stdin=source).stdout)
    restored = yaml.safe_load(state["recovery"])
    restored["unused"] = restored.pop("bad")
    restored["unused"]["$call"][-1] = 1
    restored["answer"] = {"$call": ["f", 41]}
    state["recovery"] = yaml.safe_dump(restored)
    result = h.helix("resume", "-", stdin=yaml.safe_dump(state))
    assert result.returncode == 0, result.stdout
    root = yaml.safe_load(result.stdout)["root"]
    assert root["text"] == "add" and root["items"] == ["add", "x"] and root["answer"] == 42

    for source in ['{"$call": ["div", 1, 0]}', '42', 'null', '[]', '{}']:
        result = h.helix("recover", "-", stdin=source)
        assert result.returncode in (0, 1) and not result.stderr, result
    # File-relative imports survive multiple failed attempts from a different state directory.
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory(dir=h.root / "runtime", prefix="recovery-") as directory:
        base = Path(directory)
        (base / "helper.yaml").write_text("value: 42")
        program = base / "program.yaml"
        program.write_text('{"m": {"$call": ["import", "helper.yaml"]}, "done": {"$call": ["add", {"$ref": "m.value"}, 0]}, "broken": {"$call": ["div", 1, 0]}}')
        state = yaml.safe_load(h.helix("recover", str(program)).stdout)
        checkpoint = base / "nested" / "state.yaml"
        checkpoint.parent.mkdir()
        checkpoint.write_text(yaml.safe_dump(state))
        failed = h.helix("resume", str(checkpoint))
        again = yaml.safe_load(failed.stdout)
        assert failed.returncode == 1 and again["recovery_file"] == str(program)
        restored = yaml.safe_load(again["recovery"])
        restored["broken"]["$call"][-1] = 1
        again["recovery"] = yaml.safe_dump(restored)
        checkpoint.write_text(yaml.safe_dump(again))
        result = h.helix("resume", str(checkpoint))
        assert result.returncode == 0, result
        assert yaml.safe_load(result.stdout)["root"]["done"] == 42

        (base / "helper.yaml").write_text('{"leaf": {"$call": ["fn", [], {"$call": ["div", 1, 0]}]}, "middle": {"$call": ["fn", [], {"$call": ["leaf"]}]}}')
        program.write_text('{"m": {"$call": ["import", "helper.yaml"]}, "value": {"$call": ["m.middle"]}}')
        result = h.helix("run", str(program))
        error = yaml.safe_load(result.stdout)["error"]
        assert result.returncode == 1 and error["file"].endswith("helper.yaml")
        inner, outer = error["calls"]
        assert inner["file"].endswith("helper.yaml") and inner["path"] == "/middle/$call/2"
        assert (inner["line"], inner["column"]) == location((base / "helper.yaml").read_text(), "/middle/$call/2")
        assert inner["function"] == "/leaf"
        assert "file" not in outer and outer["path"] == "/value"
        # The checkpoint parser rejects duplicate keys and never evaluates checkpoint fields.
        for invalid in ["status: error\nstatus: error\nrecovery: '42'", '{"status": {"$call": ["raise", {"$ref": "evil"}]}}']:
            result = h.helix("resume", "-", stdin=invalid)
            assert result.returncode == 1 and yaml.safe_load(result.stdout)["error"]["kind"] == "recovery"
