"""Pure operational inventory acceptance, with independent target-selection oracles."""
from copy import deepcopy
import itertools
import json
from pathlib import Path
import tempfile

import yaml


def selection(**changes):
    return {"all": False, "hosts": [], "groups": [], "exclude_hosts": [],
            "exclude_groups": [], **changes}


def document(hosts=None, groups=None, overrides=None):
    return {"version": 1, "hosts": hosts or [], "groups": groups or {},
            "overrides": overrides or []}


def host(identity, **connection):
    return {"id": identity, "connection": {"transport": "local", **connection}}


def verify(h):
    base = h.root / "runtime" / "host-inventory"
    base.mkdir(parents=True, exist_ok=True)
    artifacts = Path(tempfile.mkdtemp(dir=base, prefix="run-"))
    print(f"host_inventory replay artifacts: {artifacts.relative_to(h.root)}")
    count = 0

    def evaluate(name, inventory, dynamic=None, targets=None, pure=True):
        nonlocal count
        count += 1
        expression = {"$call": ["inventory.resolve", {"$literal": inventory},
                               {"$literal": [] if dynamic is None else dynamic},
                               {"$literal": selection(all=True) if targets is None else targets}]}
        source = json.dumps({"$call": ["let", {"inventory": {"$call": ["import", "std:inventory"]}},
                                      {"$call": ["type_time", expression]} if pure else expression]},
                            ensure_ascii=False)
        path = artifacts / f"{name}.yaml"
        path.write_text(source + "\n")
        result = h.helix("run", str(path))
        path.with_suffix(".state.yaml").write_text(result.stdout)
        assert h.malformed_state(result.stdout, result.returncode) is None, (name, result)
        state = yaml.safe_load(result.stdout)
        assert result.returncode == 0 and state["status"] == "finished", (name, state)
        report = state["root"]
        assert set(report) == {"version", "status", "targets", "diagnostics"}, (name, report)
        assert report["version"] == 1, (name, report)
        return report

    def ready(name, inventory, dynamic=None, targets=None):
        report = evaluate(name, inventory, dynamic, targets)
        assert report["status"] == "ready" and report["diagnostics"] == [], (name, report)
        return report["targets"]

    def invalid(name, inventory, dynamic=None, targets=None):
        report = evaluate(name, inventory, dynamic, targets)
        assert report["status"] == "invalid" and report["targets"] == [], (name, report)
        assert report["diagnostics"] and all(isinstance(item, dict) for item in report["diagnostics"]), (name, report)
        for diagnostic in report["diagnostics"]:
            assert {"kind", "message", "path", "line", "column"} <= diagnostic.keys(), (name, diagnostic)
        return report["diagnostics"]

    local = host("controller")
    remote = host("web", transport="ssh", address="web.example", port=2222, user="deploy", jump_host="bastion")
    dynamic = [{"source": "discovery", "hosts": [host("db", transport="ssh", address="db.example")]}]
    inventory = document([remote, local], {"all_hosts": ["web", "db", "controller"], "webservers": ["web"]})
    targets = ready("canonical", inventory, dynamic)
    assert [target["id"] for target in targets] == ["controller", "db", "web"], targets
    expected_connections = {item["id"]: item["connection"] for item in [local, remote, *dynamic[0]["hosts"]]}
    for target in targets:
        identity = target["id"]
        assert set(target) == {"id", "connection", "groups", "provenance"}, target
        assert target["connection"] == expected_connections[identity], target
        assert target["groups"] == sorted(name for name, members in inventory["groups"].items() if identity in members), target
        assert target["provenance"] == [{"source": "discovery" if identity == "db" else "static",
                                          "connection": expected_connections[identity]}], target
    assert ready("empty", document()) == []
    assert ready("no_implicit_all", inventory, dynamic, selection()) == []
    assert ready("empty_selection_defaults", inventory, dynamic, {}) == []
    assert evaluate("runtime_parity", inventory, dynamic, pure=False)["targets"] == targets
    example_path = h.root / "tests" / "programs" / "host_inventory.yaml"
    example = h.helix("run", str(example_path))
    assert h.malformed_state(example.stdout, example.returncode) is None, example
    example_state = yaml.safe_load(example.stdout)
    assert example.returncode == 0 and example_state["root"]["status"] == "ready", example_state
    assert example_state["root"]["targets"] == [{
        "id": "web", "connection": {"transport": "ssh", "address": "web.example", "user": "deploy", "port": 2222},
        "groups": ["production"], "provenance": [
            {"source": "static", "connection": {"transport": "ssh", "address": "web.example", "user": "deploy"}},
            {"source": "production-port", "connection": {"port": 2222}, "index": 0},
        ],
    }], example_state
    reversed_inventory = deepcopy(inventory)
    reversed_inventory["hosts"].reverse()
    assert ready("declaration_order", reversed_inventory, dynamic) == targets

    # An independent set oracle covers include union, exclusions after inclusion,
    # explicit all, overlapping groups, and duplicate selector idempotence.
    for index, (all_hosts, include_host, include_group, exclude_host, exclude_group) in enumerate(
            itertools.product((False, True), repeat=5)):
        query = selection(all=all_hosts, hosts=["controller", "controller"] if include_host else [],
                          groups=["all_hosts"] if include_group else [],
                          exclude_hosts=["db"] if exclude_host else [],
                          exclude_groups=["webservers"] if exclude_group else [])
        wanted = set(expected_connections) if all_hosts else set()
        wanted.update(query["hosts"])
        for group in query["groups"]:
            wanted.update(inventory["groups"][group])
        wanted.difference_update(query["exclude_hosts"])
        for group in query["exclude_groups"]:
            wanted.difference_update(inventory["groups"][group])
        actual = ready(f"selection_{index:02}", inventory, dynamic, query)
        assert [target["id"] for target in actual] == sorted(wanted), (query, actual, wanted)

    patched = deepcopy(inventory)
    patched["overrides"] = [
        {"source": "web-port", "hosts": [], "groups": ["webservers"], "connection": {"port": 2200}},
        {"source": "deploy-user", "hosts": ["web"], "groups": ["webservers"], "connection": {"user": "release"}},
        {"source": "final-port", "hosts": ["web"], "groups": [], "connection": {"port": 2201}},
    ]
    changed = ready("ordered_overrides", patched, dynamic)
    assert changed[:2] == targets[:2], changed
    assert changed[2]["connection"] == {**remote["connection"], "port": 2201, "user": "release"}, changed
    assert changed[2]["provenance"] == targets[2]["provenance"] + [
        {"source": override["source"], "connection": override["connection"], "index": index}
        for index, override in enumerate(patched["overrides"])
    ], changed
    reordered = deepcopy(patched)
    reordered["overrides"].reverse()
    assert ready("override_order_matters", reordered, dynamic)[2]["connection"]["port"] == 2200

    # Every declared input is validated before selection: exclusion cannot hide
    # corrupt hosts, dangling members, or overrides selecting unknown identities.
    assert invalid("duplicate_static", document([local, local]))[0]["kind"] == "inventory_duplicate_host"
    invalid("duplicate_dynamic", document([local]), [{"source": "external", "hosts": [local]}])
    invalid("duplicate_between_sources", document(), [{"source": name, "hosts": [local]} for name in ("a", "b")])
    for name, sources in (
        ("reserved_source", [{"source": "static", "hosts": []}]),
        ("duplicate_source", [{"source": "repeat", "hosts": []}, {"source": "repeat", "hosts": []}]),
    ):
        invalid(name, document(), sources)
    invalid("missing_group_member", document([local], {"broken": ["absent"]}), targets=selection())
    for key in ("hosts", "exclude_hosts", "groups", "exclude_groups"):
        diagnostic = invalid(f"missing_{key}", inventory, dynamic, selection(**{key: ["absent"]}))[0]
        entity = "host" if "hosts" in key else "group"
        assert diagnostic["kind"] == f"inventory_missing_{entity}" and diagnostic["data"][entity] == "absent", diagnostic
        invalid(f"missing_empty_catalog_{key}", document(), targets=selection(**{key: ["absent"]}))
    invalid("all_still_validates_hosts", inventory, dynamic, selection(all=True, hosts=["absent"]))
    invalid("all_still_validates_groups", inventory, dynamic, selection(all=True, groups=["absent"]))
    invalid("empty_group_name", document([local], {"": ["controller"]}))
    invalid("empty_dynamic_source", document(), [{"source": "", "hosts": []}])
    for key in ("hosts", "groups"):
        override = {"source": "broken", "hosts": [], "groups": [], "connection": {"port": 2200}, key: ["absent"]}
        invalid(f"missing_override_{key}", document([remote], overrides=[override]), targets=selection())
        invalid(f"missing_override_empty_catalog_{key}", document(overrides=[override]), targets=selection())

    malformed_connections = [
        {}, {"transport": "telnet"}, {"transport": "ssh"},
        {"transport": "ssh", "address": ""}, {"transport": "ssh", "address": 42},
        {"transport": "local", "address": "localhost"},
        {"transport": "local", "port": 22}, {"transport": "local", "user": "root"},
        {"transport": "local", "jump_host": "gateway"},
        {"transport": "ssh", "address": "host", "port": 0},
        {"transport": "ssh", "address": "host", "port": 65536},
        {"transport": "ssh", "address": "host", "port": True},
        {"transport": "ssh", "address": "host", "port": "22"},
        {"transport": "ssh", "address": "host", "password": "not-authority"},
    ]
    for index, connection in enumerate(malformed_connections):
        broken = document([{"id": "broken", "connection": connection}])
        invalid(f"connection_{index:02}", broken, targets=selection())
    for index, connection in enumerate(({"port": 22}, {"transport": "local"}, {"password": "secret"})):
        base = local if index == 0 else remote
        invalid(f"incompatible_override_{index}", document([base], overrides=[
            {"source": "bad-patch", "hosts": [base["id"]], "groups": [], "connection": connection}]))
    invalid("base_not_repaired", document([{"id": "web", "connection": {"transport": "ssh"}}], overrides=[
        {"source": "repair", "hosts": ["web"], "groups": [], "connection": {"address": "fixed"}}]))
    invalid("malformed_unused_patch", document(overrides=[
        {"source": "unused", "hosts": [], "groups": [], "connection": {"port": "invalid"}}]))
    switched = ready("explicit_local_to_ssh", document([local], overrides=[
        {"source": "move", "hosts": ["controller"], "groups": [], "connection": {"transport": "ssh", "address": "new.example"}}]))
    assert switched[0]["connection"] == {"transport": "ssh", "address": "new.example"}, switched
    for port in (1, 65535):
        assert ready(f"port_boundary_{port}", document([host("web", transport="ssh", address="web", port=port)]))[0]["connection"]["port"] == port
    diagnostic = invalid("typed_error_path", document([{"id": 42, "connection": {"transport": "local"}}]))[0]
    assert diagnostic["kind"] == "type", diagnostic
    assert any(violation["at"] == "/hosts/0/id" for violation in diagnostic["data"]["violations"]), diagnostic

    for index, bad in enumerate((None, [], {}, {**document(), "version": 2},
                                {**document(), "extra": True}, document([host(42)]), document([host("")]),
                                document([{**local, "extra": True}]), document([local], {"x": "controller"}))):
        invalid(f"schema_{index:02}", bad)
    for index, bad in enumerate(({}, [None], [{"source": "external", "hosts": "bad"}],
                                [{"source": "external", "hosts": [host("bad", transport="ssh")]}],
                                [{"source": "external", "hosts": [], "extra": True}])):
        invalid(f"dynamic_schema_{index:02}", document(), bad)
    for index, bad in enumerate(({**selection(), "all": "yes"}, {**selection(), "hosts": [1]},
                                {**selection(), "extra": True})):
        invalid(f"selection_schema_{index:02}", inventory, dynamic, bad)

    # Executable-looking strings and singleton maps are literal external data.
    injected = document([host("$call")], {"$ref": ["$call"]})
    assert ready("literal_identities", injected)[0]["id"] == "$call"
    opaque_id = "a/~雪.quoted'identity"
    opaque_group = "production/雪~"
    opaque = document([host(opaque_id)], {opaque_group: [opaque_id]})
    assert ready("opaque_identities", opaque, targets=selection(groups=[opaque_group]))[0]["id"] == opaque_id
    invalid("literal_executable_connection", document([{"id": "x", "connection": {"$call": ["effect", "command", {}]}}]))
    print(f"host_inventory: {count} pure/runtime scenarios; independent 32-way selection oracle; replay artifacts in runtime/host-inventory/")
