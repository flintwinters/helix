"""Resource composition contracts through the normal function/effect engine."""
import itertools
import json
from pathlib import Path
import shutil
import tempfile

import yaml


def call(actor, *arguments):
    return {"$call": [actor, *arguments]}


def ref(name):
    return {"$ref": name}


def literal(value):
    return {"$literal": value}


def document(resources=None, handlers=None):
    return {"version": 1, "resources": resources or {}, "handlers": handlers or {}}


def resource(requires=(), notify=()):
    return {"requires": list(requires), "notify": list(notify)}


def ordered(graph):
    """Independent batch-wise topological oracle, without Helix report policy."""
    remaining, completed = set(graph), []
    while remaining:
        batch = sorted(node for node in remaining if set(graph[node]) <= set(completed))
        if not batch:
            return None
        completed.extend(batch)
        remaining.difference_update(batch)
    return completed


def verify(h):
    base = h.root / "runtime" / "resource-composition"
    base.mkdir(parents=True, exist_ok=True)
    artifacts = Path(tempfile.mkdtemp(dir=base, prefix="run-"))
    print(f"resource_composition replay artifacts: {artifacts.relative_to(h.root)}")
    counter = 0

    def execute(name, expression, *, perform=False, flags=(), bindings=None):
        nonlocal counter
        counter += 1
        source = call("let", {"orchestration": call("import", "std:orchestration"), **(bindings or {})}, expression)
        path = artifacts / f"{name}.yaml"
        path.write_text(json.dumps(source, ensure_ascii=False) + "\n")
        process = h.helix("perform" if perform else "run", *flags, str(path))
        output = process.stderr if perform else process.stdout
        path.with_suffix(".state.yaml").write_text(output)
        state = yaml.safe_load(output)
        assert isinstance(state, dict), (name, process)
        if state.get("status") != "signaled":
            assert h.malformed_state(output, process.returncode) is None, (name, process)
        return process, state

    def plan(name, value):
        process, state = execute(name, call("type_time", call("orchestration.plan", literal(value))))
        assert process.returncode == 0 and state["status"] == "finished", (name, state)
        return state["root"]

    def callback(outcomes=None, body=None):
        lookup = call("get", call("get", literal(outcomes or {}), ref("kind"), {}), ref("id"), {"status": "changed"})
        logged = call("effect", "stdout", call("concat", ref("kind"), ":", ref("id"), "\n"))
        return call("fn", ["kind", "id"], call("get", call("list", logged, body or lookup), 1))

    def run(name, value, outcomes=None, body=None, *, fn=None):
        process, state = execute(name, call("orchestration.run", literal(value), fn or callback(outcomes, body)),
                                 perform=True, flags=("--allow-stdout",))
        assert process.returncode == 0 and state["status"] == "finished", (name, state)
        return state["root"], process.stdout.splitlines(), state

    empty = plan("empty_plan", document())
    assert empty == {"version": 1, "status": "ready", "order": [], "handlers": [], "diagnostics": []}, empty
    report, events, _ = run("empty_run", document())
    assert report == {"version": 1, "status": "finished", "changed": False,
                      "resources": {}, "handlers": {}, "diagnostics": []}, report
    assert events == [], events

    # Exhaust all DAGs respecting four named vertices, including disconnected
    # nodes and diamonds; source ordering is deliberately reversed.
    names = ["a", "b", "c", "d"]
    edges = [(names[i], names[j]) for i in range(4) for j in range(i)]
    for index, enabled in enumerate(itertools.product((False, True), repeat=len(edges))):
        graph = {name: [] for name in reversed(names)}
        for (node, dependency), include in zip(edges, enabled):
            if include:
                graph[node].append(dependency)
        value = document({name: resource(dependencies) for name, dependencies in graph.items()})
        actual = plan(f"dag_{index:02}", value)
        assert actual["status"] == "ready" and actual["diagnostics"] == [], (graph, actual)
        assert actual["order"] == ordered(graph), (graph, actual)
    repeated = document({"last": resource(["first", "first"]), "first": resource()})
    assert plan("repeated_dependency", repeated)["order"] == ["first", "last"]

    bad_graphs = {
        "missing_dependency": document({"ready": resource(), "blocked": resource(["absent"])}),
        "cycle": document({"ready": resource(), "a": resource(["b"]), "b": resource(["a"])}),
        "self_cycle": document({"ready": resource(), "self": resource(["self"])}),
        "missing_notification": document({"ready": resource(), "later": resource(["ready"], ["absent"])}),
        "missing_handler_dependency": document({"ready": resource()}, {"unused": {"requires": ["absent"]}}),
        "empty_resource_handler_dependency": document(handlers={"unused": {"requires": ["absent"]}}),
        "resource_requires_handler": document({"ready": resource(["restart"])}, {"restart": {"requires": []}}),
    }
    for name, value in bad_graphs.items():
        pure = plan(name + "_plan", value)
        assert pure["status"] == "invalid" and pure["order"] == [], (name, pure)
        expected_kind = "orchestration_dependency_cycle" if name in {"cycle", "self_cycle"} else "orchestration_missing_reference"
        assert pure["diagnostics"] and pure["diagnostics"][0]["kind"] == expected_kind, (name, pure)
        if name == "missing_dependency":
            assert pure["diagnostics"][0]["data"] == {"owner": "blocked", "field": "requires", "target": "absent"}, pure
        report, events, state = run(name + "_run", value)
        assert events == [] and state.get("effects", []) == [], (name, events, state)
        assert report["status"] == "invalid" and report["resources"] == {} and report["handlers"] == {}, (name, report)

    graph = document({
        "package": resource(),
        "config_a": resource(["package"], ["restart", "restart"]),
        "config_b": resource(["package"], ["restart"]),
    }, {"restart": {"requires": ["package"]}, "unused": {"requires": []}})
    report, events, state = run("coalesced", graph)
    assert events == ["resource:package", "resource:config_a", "resource:config_b", "handler:restart"], events
    assert [effect["payload"] for effect in state["effects"]] == [event + "\n" for event in events], state
    assert all(effect["status"] == "finished" and effect["operation"] == "stdout" for effect in state["effects"]), state
    assert len({effect["id"] for effect in state["effects"]}) == len(events), state
    assert report["status"] == "finished" and report["changed"] is True, report
    assert report["handlers"]["unused"] == {"status": "skipped", "reason": "not_notified"}, report
    unchanged = {"resource": {name: {"status": "unchanged"} for name in graph["resources"]}}
    report, events, _ = run("unchanged_no_handler", graph, unchanged)
    assert events == ["resource:package", "resource:config_a", "resource:config_b"], events
    assert report["status"] == "finished" and report["changed"] is False, report
    assert report["handlers"]["restart"]["reason"] == "not_notified", report

    for status in ("failed", "skipped"):
        report, events, _ = run(f"prerequisite_{status}", graph, {"resource": {"package": {"status": status}}})
        assert events == ["resource:package"], (status, events)
        assert report["status"] == "failed", report
        assert report["resources"]["config_a"] == {"status": "skipped", "reason": "prerequisite_failed"}, report
    report, events, _ = run("failed_notifier_blocks_shared_handler", graph,
                            {"resource": {"config_b": {"status": "failed", "details": "write failed"}}})
    assert events == ["resource:package", "resource:config_a", "resource:config_b"], events
    assert report["handlers"]["restart"] == {"status": "skipped", "reason": "prerequisite_failed"}, report
    assert report["status"] == "failed" and report["changed"] is True, report
    report, events, _ = run("handler_failure", graph, {"handler": {"restart": {"status": "failed"}}})
    assert report["status"] == "failed" and events.count("handler:restart") == 1, report
    report, events, _ = run("handler_skipped", graph, {"handler": {"restart": {"status": "skipped"}}})
    assert report["status"] == "failed" and events.count("handler:restart") == 1, report
    explicit_gate = document({"notifier": resource(notify=["restart"]), "gate": resource()},
                             {"restart": {"requires": ["gate"]}})
    report, events, _ = run("explicit_handler_gate", explicit_gate, {"resource": {"gate": {"status": "failed"}}})
    assert events == ["resource:gate", "resource:notifier"] and report["handlers"]["restart"]["reason"] == "prerequisite_failed", (events, report)
    two_handlers = document({"source": resource(notify=["z_restart", "a_reload"])},
                            {"z_restart": {"requires": []}, "a_reload": {"requires": []}})
    report, events, _ = run("canonical_handler_order", two_handlers)
    assert events == ["resource:source", "handler:a_reload", "handler:z_restart"], events
    handler_error = call("if", call("eq", ref("kind"), "handler"), call("raise", "reload_failed", "service rejected reload"), {"status": "changed"})
    report, events, _ = run("handler_exception", graph, body=handler_error)
    assert report["status"] == "failed" and report["handlers"]["restart"]["error"]["kind"] == "reload_failed", report
    assert events.count("handler:restart") == 1, events
    same_names = document({"same": resource(notify=["same", "same"])}, {"same": {"requires": ["same", "same"]}})
    report, events, _ = run("separate_namespaces", same_names)
    assert report["status"] == "finished" and events == ["resource:same", "handler:same"], (report, events)

    independent = document({"a": resource(), "b": resource(["a"]), "free": resource()})
    for index, bad in enumerate((None, False, [], {}, {"status": "bogus"}, {"status": "changed", "extra": 1},
                                {"status": "changed", "details": {"not": "text"}})):
        body = call("if", call("eq", ref("id"), "a"), literal(bad), {"status": "unchanged"})
        report, events, _ = run(f"malformed_callback_{index}", independent, body=body)
        assert events == ["resource:a", "resource:free"], (bad, events)
        assert report["resources"]["a"]["status"] == "failed" and report["resources"]["a"]["error"]["kind"] == "type", report
        assert report["resources"]["b"]["reason"] == "prerequisite_failed", report
    thrown = call("if", call("eq", ref("id"), "a"), call("raise", "provider_failure", "broken callback"), {"status": "unchanged"})
    report, events, _ = run("thrown_callback", independent, body=thrown)
    assert events == ["resource:a", "resource:free"], events
    assert report["resources"]["a"]["error"]["kind"] == "provider_failure", report

    malformed = [None, [], {}, {**document(), "version": 2}, {**document(), "extra": True},
                 document({"": resource()}), document({"a": {"requires": "b", "notify": []}}),
                 document({"a": {"requires": [], "notify": [42]}}),
                 document(handlers={"unused": {"requires": [], "extra": True}}),
                 document(handlers={"": {"requires": []}})]
    for index, value in enumerate(malformed):
        report, events, _ = run(f"schema_{index}", value)
        assert report["status"] == "invalid" and events == [], (value, report, events)
    process, state = execute("nonfunction_executor", call("orchestration.run", literal(document()), False), perform=True)
    assert process.returncode == 1 and state["error"]["kind"] == "type" and process.stdout == "", state

    # Observe actual writes independently, rather than trusting callback reports.
    log = artifacts / "mutations.txt"
    log.write_text("")
    append = call("effect", "file_write", call("dict", "path", str(log), "text",
                  call("concat", call("effect", "file_read", str(log)), ref("kind"), ":", ref("id"), "\n")))
    file_callback = call("fn", ["kind", "id"], call("get", call("list", append, {"status": "changed"}), 1))
    process, state = execute("file_mutations", call("orchestration.run", literal(graph), file_callback),
                             perform=True, flags=("--allow-read", str(artifacts), "--allow-write", str(artifacts)))
    assert process.returncode == 0 and state["root"]["status"] == "finished", state
    assert log.read_text().splitlines() == ["resource:package", "resource:config_a", "resource:config_b", "handler:restart"]
    assert [effect["operation"] for effect in state["effects"]] == ["file_read", "file_write"] * 4, state

    _verify_modules(h, artifacts)
    print(f"resource_composition: {counter} generated scenarios, exhaustive four-node DAG oracle and independently observed effects")


def _verify_modules(h, artifacts):
    example = h.root / "tests" / "programs" / "resource_composition.yaml"
    result = h.helix("run", str(example))
    assert result.returncode == 0 and h.malformed_state(result.stdout, result.returncode) is None, result
    original = yaml.safe_load(result.stdout)["root"]
    assert original["status"] == "finished" and original["handlers"]["restart:web"]["status"] == "changed", original
    relocated = artifacts / "relocated"
    relocated.mkdir()
    shutil.copy2(example, relocated / example.name)
    shutil.copytree(example.parent / "vendor", relocated / "vendor")
    moved = h.helix("run", str(relocated / example.name))
    assert moved.returncode == 0 and yaml.safe_load(moved.stdout)["root"] == original, moved
    invalid = relocated / "bad_input.yaml"
    invalid.write_text(json.dumps(call("let", {"config": call("import", "vendor/web-config-v1/main.yaml"),
                                               "orchestration": call("import", "std:orchestration")},
        call("orchestration.run", call("config.build", {"service": 42}),
             call("fn", ["kind", "id"], call("effect", "stdout", "must not execute"))))))
    failed = h.helix("perform", "--allow-read", str(relocated), "--allow-stdout", str(invalid))
    error = yaml.safe_load(failed.stderr)
    assert failed.returncode == 1 and error["error"]["kind"] == "type" and failed.stdout == "", error
    dependency = relocated / "vendor" / "service-policy-v1" / "main.yaml"
    dependency.unlink()
    missing = h.helix("run", str(relocated / example.name))
    error = yaml.safe_load(missing.stdout)
    assert missing.returncode == 1 and error["error"]["kind"] == "import", error
