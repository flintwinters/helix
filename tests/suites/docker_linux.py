"""Opt-in real Docker acceptance for general Debian host resources; Python observes, Helix decides.

Reuses the nginx suite's bounded Docker driver, pinned image and cleanup ownership. The
independent probe below reads actual accounts, packages and paths without the host helper.
"""
import json
from contextlib import contextmanager
import os
import random
from pathlib import Path
import signal
import subprocess
import sys
import time

from tests.suites.docker_nginx import Docker, LABEL, VERSIONS, controller_report, validate_versions


PACKAGES = json.loads((Path(__file__).resolve().parents[1] / "fixtures/docker_linux/packages.json").read_text())
HOST = "/usr/local/bin/helix-linux-host"
PROGRAM = "/opt/helix-programs/docker_linux/operate.yaml"
TRACE = "/var/lib/helix-linux-host/actions.jsonl"
FAULT = "/run/helix-linux/fault.json"
SENTINEL = "/var/lib/helix-unmanaged/sentinel"
SCENARIOS = {
    "baseline": "Pure preview, reviewed apply, independent state and two no-op cycles",
    "updates": "Changing one declared value changes only its resource",
    "drift": "Independent bytes, mode, owner, link, account and package drift repair only what changed",
    "removal": "Explicit absent state removes in reverse dependency order; omission and unmanaged state survive",
    "invalid_input": "Names, paths, missing fields, cycles and absent prerequisites fail before mutation",
    "stale_review": "Observed state, inputs, dependency graph and target identity invalidate a review",
    "failure": "A failed resource skips only its dependents; the rest converges and a rerun finishes",
    "false_success": "A command that claims success without mutating fails its postcondition",
    "authority": "Missing command authority and failed observation fail closed",
    "lifecycle": "Container recreation yields a fresh target identity and a clean baseline",
    "sensitivity": "Independent oracles reject corrupt state that a report might call converged",
    "peers": "Two container-resident Helix controllers configure each other over pinned SSH, converge and repair drift",
    "adversarial_plans": "Reject contradictory paths, self dependencies and invalid absent declarations before effects",
    "exact_files": "Preserve CRLF, CR, Unicode, NUL and empty file bytes without repeated drift",
    "host_failures": "Every resource rejects false success and real helper failures, then recovers",
    "filesystem": "Symlink ancestors, wrong object kinds, invalid UTF-8 and oversized files fail closed",
    "sequences": "Seeded state transitions, removals and drift repeatedly converge against an independent oracle",
    "peer_failures": "Pinned identity, denied grants, stale remote reviews, interrupted SSH and repair in both directions",
    "peer_recovery": "Container-resident controller death refuses uncertain replay and resumes only after exact reconciliation",
    "file_failures": "Failure before and after atomic replacement preserves evidence and permits fresh reconciliation",
    "contention": "Actual dpkg locks and conflicting account identifiers fail closed and recover",
    "observations": "Concurrent first observations publish exactly one complete target identity",
    "late_drift": "A resource changed after its own postcondition still fails whole-application verification",
    "peer_parallel": "Both container-resident controllers configure their different targets simultaneously",
    "identity_changes": "Numeric account changes rebind named ownership in one apply without unnecessary rewrites",
    "user_removal": "Deleting a user preserves separately managed and omitted groups",
}

# Deliberately independent of the host helper: reads actual OS state for a fixed superset.
PROBE = r'''
import grp, json, os, pathlib, pwd, stat, subprocess, sys
request = json.loads(sys.argv[1])
def package(name):
    run = subprocess.run(['dpkg-query', '-W', '-f=${db:Status-Status} ${Version}', name], text=True, capture_output=True, timeout=10)
    return run.stdout if run.returncode == 0 and run.stdout.startswith('installed ') else None
def group(name):
    try: return grp.getgrnam(name).gr_gid
    except KeyError: return None
def user(name):
    try:
        entry = pwd.getpwnam(name)
        return dict(uid=entry.pw_uid, gid=entry.pw_gid, home=entry.pw_dir, shell=entry.pw_shell)
    except KeyError: return None
def path(name):
    p = pathlib.Path(name)
    if p.is_symlink(): return dict(type='symlink', target=os.readlink(p))
    if not p.exists(): return None
    info = p.stat()
    common = dict(uid=info.st_uid, gid=info.st_gid, mode=stat.S_IMODE(info.st_mode), inode=info.st_ino, mtime_ns=info.st_mtime_ns)
    if p.is_dir(): return dict(type='directory', **common)
    return dict(type='file', content=p.read_bytes().decode('utf-8'), **common)
processes = {}
for p in pathlib.Path('/proc').iterdir():
    if p.name.isdigit():
        try:
            command = (p/'cmdline').read_bytes().replace(b'\0', b' ').decode(errors='replace')
            if command.startswith('sleep 86400'): processes[p.name] = command
        except (OSError, UnicodeError): pass
print(json.dumps(dict(packages={n: package(n) for n in request['packages']}, groups={n: group(n) for n in request['groups']},
                      users={n: user(n) for n in request['users']}, paths={n: path(n) for n in request['paths']}, processes=processes)))
'''
WATCHED = dict(
    packages=("jq", "tree"), groups=("helix-app", "helix-extra"), users=("helix-app", "helix-extra"),
    paths=("/etc/helix-demo", "/etc/helix-demo/app.conf", "/etc/helix-demo/current", "/etc/helix-demo/extra.conf",
           "/srv/helix", "/srv/helix/app", "/srv/helix/app/README", "/srv/helix/app/unmanaged.txt", SENTINEL))
SECTION = dict(group="groups", user="users", package="packages", directory="directories", file="files", symlink="symlinks")


def replay(name):
    return f"HELIX_DOCKER_LINUX_SCENARIO={name} ./manage.py test --suite docker_linux"


def options():
    chosen = os.environ.get("HELIX_DOCKER_LINUX_SCENARIO", "all")
    names = list(SCENARIOS) if chosen == "all" else chosen.split(",")
    if len(set(names)) != len(names) or any(name not in SCENARIOS for name in names):
        raise ValueError(f"unknown Docker Linux scenario {chosen!r}; choose one of {', '.join(SCENARIOS)}")
    repeat = int(os.environ.get("HELIX_DOCKER_LINUX_REPEAT", "1"))
    if not 1 <= repeat <= 10:
        raise ValueError("HELIX_DOCKER_LINUX_REPEAT must be between 1 and 10")
    keep = os.environ.get("HELIX_DOCKER_LINUX_KEEP_PEERS", "0")
    if keep not in {"0", "1"} or keep == "1" and (chosen != "peers" or repeat != 1):
        raise ValueError("HELIX_DOCKER_LINUX_KEEP_PEERS=1 requires SCENARIO=peers and REPEAT=1")
    return names, repeat


def desired(**changes):
    return {
        "groups": {"helix-app": {"gid": 2001}},
        "users": {"helix-app": {"uid": 2001, "group": "helix-app", "home": "/srv/helix/app"}},
        "packages": {"jq": {"version": PACKAGES["jq"]}, "tree": {"version": PACKAGES["tree"]}},
        "directories": {"/etc/helix-demo": {}, "/srv/helix": {},
                        "/srv/helix/app": {"owner": "helix-app", "group": "helix-app", "mode": 0o750}},
        "files": {"/etc/helix-demo/app.conf": {"content": "listen = 8080\n", "group": "helix-app", "mode": 0o640},
                  "/srv/helix/app/README": {"content": "Managed by Helix\n", "owner": "helix-app", "group": "helix-app"}},
        "symlinks": {"/etc/helix-demo/current": {"target": "/srv/helix/app"}},
        **changes}


def expected(inputs):
    """Independent workload oracle: the declared policy and its documented defaults, never a report."""
    groups = {name: spec["gid"] for name, spec in inputs.get("groups", {}).items() if spec.get("state") != "absent"}
    users = {name: spec for name, spec in inputs.get("users", {}).items() if spec.get("state") != "absent"}
    paths = {}
    for path, spec in inputs.get("directories", {}).items():
        if spec.get("state") != "absent":
            paths[path] = dict(type="directory", owner=spec.get("owner", "root"), group=spec.get("group", "root"), mode=spec.get("mode", 0o755))
    for path, spec in inputs.get("files", {}).items():
        if spec.get("state") != "absent":
            paths[path] = dict(type="file", owner=spec.get("owner", "root"), group=spec.get("group", "root"), mode=spec.get("mode", 0o644), content=spec["content"])
    for path, spec in inputs.get("symlinks", {}).items():
        if spec.get("state") != "absent":
            paths[path] = dict(type="symlink", target=spec["target"])
    packages = {name: "installed " + spec["version"] for name, spec in inputs.get("packages", {}).items() if spec.get("state") != "absent"}
    return groups, users, paths, packages


def dependents(review, resource):
    """Transitive dependents in the reviewed graph: the resources a failed prerequisite must skip."""
    graph = review["dependencies"]["resources"]
    skipped, pending = set(), [resource]
    while pending:
        current = pending.pop()
        for name, node in graph.items():
            if current in node["requires"] and name not in skipped:
                skipped.add(name)
                pending.append(name)
    return skipped


class LinuxDocker(Docker):
    """The shared bounded Docker driver with this workload's container, probe and controller."""

    fixture = "docker_linux"

    def fresh(self, name=None):
        if self.container:
            self.command("rm", "-f", self.container)
        self.container = name or f"helix-linux-{self.run_id}"
        self.command("run", "-d", "--init", "--name", self.container, "--label", f"{LABEL}={self.run_id}",
                     "--platform", VERSIONS["platform"], "--network", self.network, "--pids-limit", "256", "--memory", "512m", self.image)
        metadata = json.loads(self.command("inspect", self.container).stdout)[0]
        assert metadata["HostConfig"]["Privileged"] is False and metadata["HostConfig"]["PidMode"] != "host", metadata["HostConfig"]
        assert not metadata["Mounts"], "fixture must not mount host paths or Docker sockets"
        (self.artifacts / f"container-{self.serial:04d}.json").write_text(json.dumps(metadata, indent=2))
        self.command("exec", "-d", self.container, "sleep", "86400")
        state = self.probe()
        self.sentinel = state["paths"][SENTINEL]
        self.unmanaged = dict(state["processes"])
        assert self.sentinel and self.unmanaged, "unmanaged sentinel file and process must exist"

    def probe(self):
        state = json.loads(self.exec("python3", "-c", PROBE, json.dumps(WATCHED)).stdout)
        self.serial += 1
        (self.artifacts / f"observation-{self.serial:04d}.json").write_text(json.dumps(state, indent=2))
        return state

    def actions(self):
        result = self.exec("cat", TRACE, check=False)
        return [json.loads(line) for line in result.stdout.splitlines()] if result.returncode == 0 else []

    def performed(self, since):
        return sorted((row["resource"], row["action"]) for row in self.actions()[since:])

    def fault(self, value=None):
        self.exec("mkdir", "-p", "/run/helix-linux")
        if value is not None:
            self.exec("rm", "-f", "/run/helix-linux/fault-reached.json", "/run/helix-linux/fault-release")
        if value is None:
            self.exec("rm", "-f", FAULT)
        else:
            self.put(FAULT, json.dumps(value))

    def controller_args(self, mode, inputs, review=None, *, grant=True, timeout_ms=120000):
        return ["helix", "perform", "--allow-stdin", "--allow-stdout", *(["--allow-command", HOST] if grant else []),
                "--command-timeout-ms", str(timeout_ms), PROGRAM]

    def preserved(self):
        state = self.probe()
        assert state["paths"][SENTINEL] == self.sentinel, state
        assert all(state["processes"].get(pid) == command for pid, command in self.unmanaged.items()), state

    def converged(self, inputs):
        state = self.probe()
        groups, users, paths, packages = expected(inputs)
        for name in WATCHED["groups"]:
            assert state["groups"][name] == groups.get(name), (name, state["groups"])
        for name in WATCHED["users"]:
            actual, want = state["users"][name], users.get(name)
            if want is None:
                assert actual is None, (name, actual)
            else:
                gid = groups[want["group"]] if want["group"] in groups else state["groups"][want["group"]]
                assert actual and (actual["uid"], actual["gid"], actual["home"], actual["shell"]) == (want["uid"], gid, want["home"], want.get("shell", "/usr/sbin/nologin")), (name, actual)
        for name in WATCHED["packages"]:
            assert state["packages"][name] == packages.get(name), (name, state["packages"])
        owners = {"root": 0, **{name: spec["uid"] for name, spec in users.items()}}
        for path in WATCHED["paths"]:
            if path in (SENTINEL, "/srv/helix/app/unmanaged.txt"):
                continue
            actual, want = state["paths"][path], paths.get(path)
            if want is None:
                assert actual is None, (path, actual)
                continue
            assert actual and actual["type"] == want["type"], (path, actual, want)
            if want["type"] == "symlink":
                assert actual["target"] == want["target"], (path, actual)
                continue
            assert (actual["uid"], actual["gid"], actual["mode"]) == (owners[want["owner"]], {"root": 0, **groups}[want["group"]], want["mode"]), (path, actual, want)
            if want["type"] == "file":
                assert actual["content"] == want["content"], (path, actual)
        self.preserved()
        return state

    def apply(self, inputs, *, expected="changed", review=None):
        report = super().apply(inputs, expected=expected, review=review)
        self.converged(inputs)
        return report


def assert_failure(report, kind, phase=None, **expect):
    assert report["status"] == "failed" and report.get("phase") == phase, report
    error = report["error"]
    assert error["kind"] == kind, error
    for key, value in expect.items():
        assert value in json.dumps(error), (key, value, error)
    return error


def resource_failure(report, review, resource, *, kind):
    """One resource fails with its real cause; exactly its transitive dependents are skipped."""
    error = assert_failure(report, "linux_application", "apply")
    resources = error["data"]["resources"]
    assert resources[resource]["status"] == "failed", resources
    assert resources[resource]["error"]["kind"] == kind, resources[resource]
    skipped = dependents(review, resource)
    for name in skipped:
        assert resources[name] == {"status": "skipped", "reason": "prerequisite_failed"}, (name, resources[name])
    for name, outcome in resources.items():
        if name != resource and name not in skipped:
            assert outcome["status"] in {"changed", "unchanged"}, (name, outcome)
    return resources


def baseline(d):
    inputs = desired()
    before = d.probe()
    assert all(value is None for value in (*before["packages"].values(), *before["groups"].values(), *before["users"].values()))
    assert before["paths"]["/srv/helix"] is None, before
    actions = d.actions()
    assert d.controller("resolve", inputs) == inputs
    explained = d.controller("explain", inputs)
    assert explained["format"] == "helix.linux.configuration.v1" and explained["values"] == inputs, explained
    observation = d.controller("observe", inputs)
    assert "resources" in observation, observation
    assert set(observation["resources"]) == {"group:helix-app", "user:helix-app", "package:jq", "package:tree", "directory:/etc/helix-demo",
                                             "directory:/srv/helix", "directory:/srv/helix/app", "file:/etc/helix-demo/app.conf",
                                             "file:/srv/helix/app/README", "symlink:/etc/helix-demo/current"}, observation
    assert all(value is None for value in observation["resources"].values()), observation
    pure = d.controller("plan", inputs, observation)
    first, second = d.controller("check", inputs), d.controller("check", inputs)
    assert pure == first == second and first["status"] == "changed", (pure, first, second)
    assert len(first["steps"]) == 10 and {step["op"] for step in first["steps"]} == {"set"}, first
    requires = first["dependencies"]["resources"]
    assert requires["user:helix-app"]["requires"] == ["group:helix-app"], requires
    assert "directory:/srv/helix" in requires["directory:/srv/helix/app"]["requires"], requires
    assert {"user:helix-app", "group:helix-app", "directory:/srv/helix/app"} <= set(requires["file:/srv/helix/app/README"]["requires"]), requires
    assert requires["package:jq"]["requires"] == [], requires
    assert d.probe() == before and d.actions() == actions, "preview mutated the host"
    d.apply(inputs, review=first)
    created = {resource for resource, action in d.performed(len(actions)) if action == "set"}
    assert created == set(observation["resources"]), created
    for _ in range(2):
        state, trace = d.probe(), d.actions()
        d.apply(inputs, expected="unchanged")
        assert d.controller("verify", inputs)["status"] == "unchanged"
        assert d.probe() == state and d.actions() == trace, "no-op mutated actual state or emitted an action"


def updates(d):
    inputs = desired()
    d.apply(inputs)
    def field(section, name, **values):
        return lambda current: {section: {**current[section], name: {**current[section][name], **values}}}
    cases = [
        ("file content", field("files", "/etc/helix-demo/app.conf", content="listen = 9090\n"), [("file:/etc/helix-demo/app.conf", "set")]),
        ("file mode", field("files", "/srv/helix/app/README", mode=0o600), [("file:/srv/helix/app/README", "set")]),
        ("directory mode", field("directories", "/srv/helix", mode=0o700), [("directory:/srv/helix", "set")]),
        ("symlink target", field("symlinks", "/etc/helix-demo/current", target="/srv/helix"), [("symlink:/etc/helix-demo/current", "set")]),
        ("user shell", field("users", "helix-app", shell="/bin/sh"), [("user:helix-app", "set")]),
        ("new file", lambda current: {"files": {**current["files"], "/etc/helix-demo/extra.conf": {"content": "extra\n"}}}, [("file:/etc/helix-demo/extra.conf", "set")]),
    ]
    current = inputs
    for name, change, resources in cases:
        current = {**current, **change(current)}
        since = len(d.actions())
        d.apply(current)
        assert d.performed(since) == resources, (name, d.performed(since))
        d.apply(current, expected="unchanged")


def drift(d):
    inputs = desired()
    d.apply(inputs)
    changes = [
        ("bytes", ["python3", "-c", "from pathlib import Path; p=Path('/etc/helix-demo/app.conf'); p.write_text(p.read_text()+'# drift\\n')"], [("file:/etc/helix-demo/app.conf", "set")]),
        ("mode", ["chmod", "600", "/srv/helix/app/README"], [("file:/srv/helix/app/README", "set")]),
        ("owner", ["chown", "root:root", "/srv/helix/app/README"], [("file:/srv/helix/app/README", "set")]),
        ("directory mode", ["chmod", "777", "/srv/helix"], [("directory:/srv/helix", "set")]),
        ("link target", ["ln", "-sfn", "/tmp", "/etc/helix-demo/current"], [("symlink:/etc/helix-demo/current", "set")]),
        ("missing file", ["rm", "-f", "/etc/helix-demo/app.conf"], [("file:/etc/helix-demo/app.conf", "set")]),
        ("account shell", ["usermod", "-s", "/bin/sh", "helix-app"], [("user:helix-app", "set")]),
        ("package", ["apt-get", "-y", "remove", "jq"], [("package:jq", "set")]),
    ]
    for name, command, repairs in changes:
        d.exec(*command)
        review = d.controller("check", inputs)
        assert review["status"] == "changed" and sorted(step["field"] for step in review["steps"]) == sorted(r for r, _ in repairs), (name, review["steps"])
        since = len(d.actions())
        d.apply(inputs, review=review)
        assert d.performed(since) == repairs, (name, d.performed(since))
        d.apply(inputs, expected="unchanged")
    # Extra unmanaged files inside a managed directory are not drift.
    d.exec("sh", "-c", "echo keep > /srv/helix/app/unmanaged.txt")
    d.apply(inputs, expected="unchanged")
    assert d.probe()["paths"]["/srv/helix/app/unmanaged.txt"]["content"] == "keep\n"


def removal(d):
    inputs = desired()
    d.apply(inputs)
    d.exec("sh", "-c", "echo keep > /srv/helix/app/unmanaged.txt")
    # Omission never deletes: a smaller declaration plans no removal and changes nothing.
    smaller = {"files": {"/etc/helix-demo/app.conf": inputs["files"]["/etc/helix-demo/app.conf"]}}
    state, trace = d.probe(), d.actions()
    review = d.controller("check", smaller)
    assert review["status"] == "unchanged" and review["steps"] == [], review
    assert d.probe() == state and d.actions() == trace
    # Explicit removal of a file, its link and the package runs dependents-first.
    pruned = {**inputs, "files": {**inputs["files"], "/srv/helix/app/README": {**inputs["files"]["/srv/helix/app/README"], "state": "absent"}},
              "symlinks": {"/etc/helix-demo/current": {"target": "/srv/helix/app", "state": "absent"}},
              "packages": {**inputs["packages"], "tree": {**inputs["packages"]["tree"], "state": "absent"}}}
    since = len(d.actions())
    review = d.controller("check", pruned)
    assert {step["op"] for step in review["steps"]} == {"remove"} and len(review["steps"]) == 3, review
    d.apply(pruned)
    assert d.performed(since) == sorted([("file:/srv/helix/app/README", "remove"), ("symlink:/etc/helix-demo/current", "remove"), ("package:tree", "remove")])
    d.apply(pruned, expected="unchanged")
    # Removing the directory fails safely while an unmanaged file remains inside it, and skips nothing else.
    blocked = {**pruned, "directories": {**pruned["directories"], "/srv/helix/app": {**pruned["directories"]["/srv/helix/app"], "state": "absent"}},
               "files": {"/etc/helix-demo/app.conf": pruned["files"]["/etc/helix-demo/app.conf"]}}
    review = d.controller("check", blocked)
    report = d.controller("apply", blocked, review)
    resource_failure(report, review, "directory:/srv/helix/app", kind="linux_host")
    assert d.probe()["paths"]["/srv/helix/app/unmanaged.txt"]["content"] == "keep\n"
    d.exec("rm", "/srv/helix/app/unmanaged.txt")
    # With the account, group and directory all declared absent, removal follows reverse dependencies.
    gone = {**blocked, "users": {"helix-app": {**inputs["users"]["helix-app"], "state": "absent"}},
            "groups": {"helix-app": {"gid": 2001, "state": "absent"}},
            "files": {"/etc/helix-demo/app.conf": {"content": "listen = 8080\n", "state": "absent"}}}
    d.apply(gone)
    d.apply(gone, expected="unchanged")
    state = d.probe()
    assert state["users"]["helix-app"] is None and state["groups"]["helix-app"] is None, state
    assert state["paths"]["/srv/helix"] is not None, "undeclared-absent directory must survive"


def invalid_input(d):
    before, actions = d.probe(), d.actions()
    base = desired()
    bad = {
        "uppercase account": {**base, "groups": {"Helix": {"gid": 1}}},
        "relative path": {**base, "files": {"etc/app.conf": {"content": "x"}}},
        "dotdot path": {**base, "files": {"/etc/helix-demo/../passwd": {"content": "x"}}},
        "trailing slash": {**base, "directories": {"/srv/helix/": {}}},
        "root path": {**base, "directories": {"/": {}}},
        "mode too large": {**base, "files": {"/etc/helix-demo/x": {"content": "x", "mode": 4096}}},
        "unknown field": {**base, "files": {"/etc/helix-demo/x": {"content": "x", "chmod": "+x"}}},
        "unknown section": {**base, "services": {"nginx": {}}},
        "missing content": {**base, "files": {"/etc/helix-demo/x": {}}},
        "missing gid": {**base, "groups": {"helix-app": {}}},
        "missing version": {**base, "packages": {"jq": {}}},
        "bad version": {**base, "packages": {"jq": {"version": "1 2"}}},
        "unknown after": {**base, "files": {"/etc/helix-demo/x": {"content": "x", "after": ["package:missing"]}}},
        "cycle": {**base, "files": {"/etc/helix-demo/a": {"content": "x", "after": ["file:/etc/helix-demo/b"]}, "/etc/helix-demo/b": {"content": "x", "after": ["file:/etc/helix-demo/a"]}}},
        "absent prerequisite": {**base, "groups": {"helix-app": {"gid": 2001, "state": "absent"}}},
    }
    for name, inputs in bad.items():
        # Field and type errors fail at resolution; graph errors surface when the graph is built.
        for mode in (("check",) if name in {"unknown after", "cycle", "absent prerequisite"} else ("resolve", "check")):
            report = d.controller(mode, inputs)
            assert report.get("status") == "failed" and report["error"]["kind"] in {"type", "linux_invalid_spec", "linux_dependency", "orchestration_missing_reference", "orchestration_cycle", "cycle", "missing", "conflict"}, (name, mode, report)
        assert d.probe() == before and d.actions() == actions, f"{name} mutated before rejection"


def stale_review(d):
    inputs = desired()
    d.apply(inputs)
    changed = {**inputs, "files": {**inputs["files"], "/etc/helix-demo/app.conf": {**inputs["files"]["/etc/helix-demo/app.conf"], "content": "reviewed\n"}}}
    reviewed = d.controller("check", changed)
    d.exec("chmod", "600", "/srv/helix/app/README")
    state, trace = d.probe(), d.actions()
    assert_failure(d.controller("apply", changed, reviewed), "provider_stale_plan", "preflight")
    assert d.probe() == state and d.actions() == trace, "stale observed state applied"
    d.apply(changed)
    review = d.controller("check", {**changed, "files": {**changed["files"], "/etc/helix-demo/app.conf": {"content": "again\n", "group": "helix-app", "mode": 0o640}}})
    state, trace = d.probe(), d.actions()
    assert_failure(d.controller("apply", changed, review), "provider_stale_plan", "preflight")
    assert_failure(d.controller("apply", changed, {**review, "revision": "older"}), "provider_stale_plan", "preflight")
    tampered = {**review, "dependencies": {**review["dependencies"], "resources": {**review["dependencies"]["resources"], "package:jq": {"requires": ["package:tree"], "notify": []}}}}
    assert_failure(d.controller("apply", {**changed, "files": {**changed["files"], "/etc/helix-demo/app.conf": {"content": "again\n", "group": "helix-app", "mode": 0o640}}}, tampered), "linux_review_dependencies", "preflight")
    assert d.probe() == state and d.actions() == trace
    d.fresh()
    assert_failure(d.controller("apply", changed, review), "provider_stale_plan", "preflight")
    assert d.actions() == []


def failure(d):
    inputs = desired()
    review = d.controller("check", inputs)
    d.fault({"group:helix-app": "fail"})
    report = d.controller("apply", inputs, review)
    d.fault()
    resources = resource_failure(report, review, "group:helix-app", kind="linux_host")
    assert "injected group:helix-app failure" in json.dumps(resources["group:helix-app"]), resources
    state = d.probe()
    assert state["groups"]["helix-app"] is None and state["users"]["helix-app"] is None, state
    # Independent branches still converged; the failed group's dependents did not run.
    assert state["packages"]["jq"] == "installed " + PACKAGES["jq"] and state["paths"]["/srv/helix"] is not None, state
    assert state["paths"]["/srv/helix/app"] is None and state["paths"]["/srv/helix/app/README"] is None, state
    d.preserved()
    # Package repository failure surfaces apt's real cause and leaves unrelated resources alone.
    d.apply(inputs)
    d.exec("apt-get", "-y", "remove", "tree")
    review = d.controller("check", inputs)
    d.fault({"package:tree": "repository"})
    report = d.controller("apply", inputs, review)
    d.fault()
    resources = resource_failure(report, review, "package:tree", kind="linux_host")
    assert "127.0.0.1:1" in json.dumps(resources["package:tree"]), resources
    d.apply(inputs)


def false_success(d):
    inputs = desired()
    review = d.controller("check", inputs)
    d.fault({"group:helix-app": "false-success"})
    report = d.controller("apply", inputs, review)
    d.fault()
    resources = resource_failure(report, review, "group:helix-app", kind="linux_resource_postcondition")
    assert resources["group:helix-app"]["error"]["data"]["result"]["fixture_false_success"] is True, resources
    assert d.probe()["groups"]["helix-app"] is None and d.probe()["users"]["helix-app"] is None
    d.apply(inputs)


def authority(d):
    before, actions = d.probe(), d.actions()
    assert_failure(d.controller("check", desired(), grant=False), "capability", "observe", cause="not granted")
    for mode in ("fail", "malformed", "error-malformed", "error-scalar", "missing", "extra", "invalid-value"):
        d.fault({"observe": mode})
        report = d.controller("check", desired())
        assert report["status"] == "failed" and report["phase"] == "observe", (mode, report)
        if mode.startswith("error-"):
            assert report["error"]["kind"] == "linux_host" and report["error"]["data"]["code"] == 1, report
            assert report["error"]["data"]["host_error"] is None, report
        d.fault()
    assert d.probe() == before and d.actions() == actions
    # Ownership boundaries: the helper refuses paths outside the fixture roots even if Helix asked.
    outside = {"files": {"/etc/passwd": {"content": "root::0:0::/:/bin/sh\n"}}}
    report = d.controller("check", outside)
    assert report["status"] == "failed" and "outside owned fixture roots" in json.dumps(report), report
    assert d.exec("cat", "/etc/passwd").stdout.startswith("root:x:0:0")
    assert d.probe() == before and d.actions() == actions


def lifecycle(d):
    d.apply(desired())
    d.command("restart", d.container)
    d.command("exec", "-d", d.container, "sleep", "86400")
    state = d.probe()
    d.unmanaged = dict(state["processes"])
    d.apply(desired(), expected="unchanged")
    d.fresh()
    baseline(d)


def sensitivity(d):
    d.apply(desired())
    d.exec("chmod", "666", "/etc/helix-demo/app.conf")
    try:
        d.converged(desired())
    except AssertionError:
        pass
    else:
        raise AssertionError("independent mode oracle accepted drift")
    d.apply(desired())
    d.exec("userdel", "helix-app")
    try:
        d.converged(desired())
    except AssertionError:
        pass
    else:
        raise AssertionError("independent account oracle accepted a missing user")
    d.exec("groupadd", "-g", "2005", "helix-extra")
    try:
        d.converged(desired())
    except AssertionError:
        pass
    else:
        raise AssertionError("independent oracle accepted an undeclared account")
    d.exec("groupdel", "helix-extra")
    d.apply(desired())


def adversarial_configurations():
    return {
        "self dependency": {"groups": {"helix-app": {"gid": 2001, "after": ["group:helix-app"]}}},
        "two-node cycle": {"groups": {"helix-app": {"gid": 2001, "after": ["group:helix-extra"]},
                                      "helix-extra": {"gid": 2002, "after": ["group:helix-app"]}}},
        "absent unknown prerequisite": {"groups": {"helix-app": {"state": "absent", "after": ["group:missing"]}}},
        "absent self dependency": {"groups": {"helix-app": {"state": "absent", "after": ["group:helix-app"]}}},
        "file and directory": {"files": {"/etc/helix-demo": {"content": "x"}}, "directories": {"/etc/helix-demo": {}}},
        "file ancestor": {"files": {"/etc/helix-demo": {"content": "x"}, "/etc/helix-demo/app.conf": {"content": "y"}}},
        "symlink ancestor": {"symlinks": {"/etc/helix-demo": {"target": "/srv/helix"}}, "files": {"/etc/helix-demo/app.conf": {"content": "y"}}},
        "package option": {"packages": {"--help": {"version": "1"}}},
    }


def declaration_error(name):
    if name == "package option":
        return "type"
    return "linux_path_conflict" if name in {"file and directory", "file ancestor", "symlink ancestor"} else "linux_dependency"


def adversarial_plans(d):
    before, trace = d.probe(), d.actions()
    for name, inputs in adversarial_configurations().items():
        for mode in ("resolve", "check"):
            report = d.controller(mode, inputs, grant=False)
            assert report.get("status") == "failed" and report["error"]["kind"] == declaration_error(name), (name, mode, report)
        assert d.probe() == before and d.actions() == trace, (name, "changed host before rejection")
    # Removal needs only identity and absent state, not creation-only fields.
    minimal = {"users": {"helix-app": {"state": "absent"}}, "groups": {"helix-app": {"state": "absent"}}}
    assert d.controller("check", minimal)["status"] == "unchanged"
    d.apply(desired())
    removal = {**desired(), **minimal,
               "directories": {"/etc/helix-demo": {}, "/srv/helix": {}, "/srv/helix/app": {"state": "absent"}},
               "files": {"/etc/helix-demo/app.conf": {"state": "absent"}, "/srv/helix/app/README": {"state": "absent"}},
               "symlinks": {"/etc/helix-demo/current": {"state": "absent"}}}
    # Explicit account dependency is necessary when omitted removal fields carry no group evidence.
    removal["users"]["helix-app"]["after"] = ["group:helix-app"]
    review = d.controller("check", removal)
    report = d.controller("apply", removal, review)
    assert report["status"] == "changed", report
    state = d.probe()
    assert state["users"]["helix-app"] is None and state["groups"]["helix-app"] is None, state


def exact_files(d):
    inputs = desired()
    d.apply(inputs)
    for content in ("line one\r\nline two\r\n", "a\rb\r", "café λ 😀\n", "embedded\0NUL\n", "", "no newline"):
        inputs = {**inputs, "files": {**inputs["files"], "/etc/helix-demo/app.conf": {
            **inputs["files"]["/etc/helix-demo/app.conf"], "content": content}}}
        d.apply(inputs)
        raw = d.exec("python3", "-c", "from pathlib import Path; print(Path('/etc/helix-demo/app.conf').read_bytes().hex())").stdout.strip()
        assert raw == content.encode("utf-8").hex(), (content, raw)
        before, trace = d.probe(), d.actions()
        d.apply(inputs, expected="unchanged")
        assert d.probe() == before and d.actions() == trace
    # Atomic replacement must not modify another name for the old inode.
    d.exec("ln", "/etc/helix-demo/app.conf", "/srv/helix/app/unmanaged.txt")
    old = d.probe()["paths"]["/srv/helix/app/unmanaged.txt"]
    inputs["files"]["/etc/helix-demo/app.conf"]["content"] = "new inode\n"
    d.apply(inputs)
    assert d.probe()["paths"]["/srv/helix/app/unmanaged.txt"] == old
    # The framing contract has its own acceptance: exercise the real Helix
    # helper module above argv limits without misrepresenting this as provider
    # admission/application of the same oversized workload.
    value = inputs["files"]["/etc/helix-demo/app.conf"]
    for content in ("\0" * 22000, value["content"]):
        request = {"operation": "apply", "payload": {"kind": "file", "key": "/etc/helix-demo/app.conf",
                   "value": {**value, "owner": "root", "content": content}}}
        if len(content) > 20000:
            assert len(json.dumps(request["payload"]).encode()) > 131072
        result = d.command("exec", "-i", d.container, "helix", "perform", "--allow-stdin", "--allow-stdout",
                           "--allow-command", HOST, "--command-timeout-ms", "120000",
                           "/opt/helix-programs/docker_linux/helper.yaml", input_text=json.dumps(request), check=False)
        assert controller_report(result)["completed"] is True
        raw = d.exec("python3", "-c", "from pathlib import Path; print(Path('/etc/helix-demo/app.conf').read_bytes().hex())").stdout.strip()
        assert raw == content.encode().hex()
    d.converged(inputs)
    # Capacity probing is separate from acceptance: metadata-only work must not
    # traverse file bodies, and a larger plan must either finish or hit an
    # explicit runtime budget without performing mutations. Do not disguise a
    # limit as successful application of this 140 KB combined manifest.
    capacity = {**inputs, "files": {**inputs["files"], "/etc/helix-demo/app.conf": {
        **inputs["files"]["/etc/helix-demo/app.conf"], "content": "x" * 140000}}}
    assert d.controller("graph", capacity, grant=False) == d.controller("graph", inputs, grant=False)
    before, trace = d.probe(), d.actions()
    report = d.controller("check", capacity)
    assert report["status"] == "changed" or report["status"] == "failed" and report["error"]["kind"] == "limit", report
    assert d.probe() == before and d.actions() == trace, "capacity probe mutated host"
    (d.artifacts / "inline-capacity.json").write_text(json.dumps({"bytes": 140000, "check_status": report["status"],
        "error_kind": report.get("error", {}).get("kind"), "application_qualified": False, "host_unchanged": True}, indent=2))


def identity_changes(d):
    inputs = desired()
    d.apply(inputs)
    changes = [
        ("gid", "groups", "helix-app", {"gid": 2002}, None),
        ("uid in home", "users", "helix-app", {"uid": 2002}, {"user:helix-app"}),
        ("move home", "users", "helix-app", {"home": "/home/helix-app"}, {"user:helix-app"}),
        ("uid outside home", "users", "helix-app", {"uid": 2003},
         {"user:helix-app", "directory:/srv/helix/app", "file:/srv/helix/app/README"}),
        ("gid back", "groups", "helix-app", {"gid": 2001}, None),
    ]
    for name, section, key, fields, actual in changes:
        inputs = {**inputs, section: {**inputs[section], key: {**inputs[section][key], **fields}}}
        review = d.controller("check", inputs)
        since = len(d.actions())
        d.apply(inputs, review=review)
        changed = {resource for resource, operation in d.performed(since)}
        allowed = {"user:helix-app", "group:helix-app", "directory:/srv/helix/app",
                   "file:/srv/helix/app/README", "file:/etc/helix-demo/app.conf"}
        assert changed <= allowed and (actual is None or changed == actual), (name, changed, actual)
        before, trace = d.probe(), d.actions()
        d.apply(inputs, expected="unchanged")
        assert d.probe() == before and d.actions() == trace, (name, "repeat changed state")


def user_removal(d):
    seed = {"groups": desired()["groups"], "users": desired()["users"]}
    for retain in (True, False):
        d.fresh()
        d.apply(seed)
        absent = {"users": {"helix-app": {"state": "absent"}},
                  **({"groups": seed["groups"]} if retain else {})}
        review = d.controller("check", absent)
        report = d.controller("apply", absent, review)
        assert report["status"] == "changed", report
        state = d.probe()
        assert state["users"]["helix-app"] is None and state["groups"]["helix-app"] == 2001, (retain, state)
        assert d.controller("check", absent)["status"] == "unchanged"
        d.preserved()
    d.fresh()
    d.apply(seed)
    policy = d.exec("cat", "/etc/login.defs").stdout
    assert "USERGROUPS_ENAB no" in policy
    d.exec("python3", "-c", "from pathlib import Path; p=Path('/etc/login.defs'); p.write_text(p.read_text().replace('USERGROUPS_ENAB no','USERGROUPS_ENAB yes'))")
    absent = {"users": {"helix-app": {"state": "absent"}}, "groups": seed["groups"]}
    before, trace = d.probe(), d.actions()
    review = d.controller("check", absent)
    report = d.controller("apply", absent, review)
    failures = resource_failure(report, review, "user:helix-app", kind="linux_host")
    assert "USERGROUPS_ENAB" in json.dumps(failures), failures
    assert d.probe() == before and d.actions() == trace, "unsafe userdel policy mutated accounts"
    d.exec("python3", "-c", "from pathlib import Path; import sys; Path('/etc/login.defs').write_text(sys.argv[1])", policy)


def host_failures(d):
    inputs = desired()
    for resource in ("group:helix-app", "user:helix-app", "package:jq", "directory:/srv/helix/app",
                     "file:/etc/helix-demo/app.conf", "symlink:/etc/helix-demo/current"):
        for mode in ("fail", "false-success"):
            d.fresh()
            review = d.controller("check", inputs)
            d.fault({resource: mode})
            report = d.controller("apply", inputs, review)
            d.fault()
            resource_failure(report, review, resource,
                             kind="linux_host" if mode == "fail" else "linux_resource_postcondition")
            d.apply(inputs)
            d.apply(inputs, expected="unchanged")
    # Deletion has different dependency and OS semantics. Isolate each kind so
    # a prerequisite's native side effects cannot satisfy the faulted removal
    # before its callback is reached (userdel can remove a private group).
    removals = [
        ("group", "helix-extra", {"groups": {"helix-extra": {"gid": 2009}}}),
        ("user", "helix-app", {"groups": inputs["groups"], "users": inputs["users"]}),
        ("package", "jq", {"packages": {"jq": inputs["packages"]["jq"]}}),
        ("directory", "/etc/helix-demo", {"directories": {"/etc/helix-demo": {}}}),
        ("file", "/etc/helix-demo/app.conf", {"directories": {"/etc/helix-demo": {}},
                                              "files": {"/etc/helix-demo/app.conf": {"content": "remove me\n"}}}),
        ("symlink", "/etc/helix-demo/current", {"directories": {"/etc/helix-demo": {}},
                                               "symlinks": {"/etc/helix-demo/current": {"target": "/srv/helix"}}}),
    ]
    for kind, key, seed in removals:
        for mode in ("fail", "false-success"):
            d.fresh()
            d.apply(seed)
            absent = {SECTION[kind]: {key: {"state": "absent"}}}
            review = d.controller("check", absent)
            d.fault({f"{kind}:{key}": mode})
            report = d.controller("apply", absent, review)
            d.fault()
            resource_failure(report, review, f"{kind}:{key}",
                             kind="linux_host" if mode == "fail" else "linux_resource_postcondition")
            checked = d.controller("check", absent)
            report = d.controller("apply", absent, checked)
            assert report["status"] == "changed", report
            state = d.probe()
            assert state[SECTION[kind] if kind in {"group", "user", "package"} else "paths"][key] is None, (kind, state)
            d.preserved()
            d.apply(seed)


def filesystem(d):
    inputs = desired()
    d.apply(inputs)
    cases = [
        ("symlink ancestor", "import os; os.rename('/etc/helix-demo','/etc/helix-demo-original'); os.symlink('/etc/helix-demo-original','/etc/helix-demo')",
         "import os; os.unlink('/etc/helix-demo'); os.rename('/etc/helix-demo-original','/etc/helix-demo')"),
        ("wrong object", "from pathlib import Path; p=Path('/etc/helix-demo/app.conf'); p.unlink(); p.mkdir()",
         "from pathlib import Path; Path('/etc/helix-demo/app.conf').rmdir()"),
        ("invalid utf8", "from pathlib import Path; Path('/etc/helix-demo/app.conf').write_bytes(b'\\xff')", None),
        ("oversized file", "from pathlib import Path; Path('/etc/helix-demo/app.conf').write_bytes(b'x'*1048577)", None),
    ]
    for name, corrupt, restore in cases:
        d.exec("python3", "-c", corrupt)
        trace = d.actions()
        for mode in ("check", "apply"):
            report = d.controller(mode, inputs, {} if mode == "apply" else None)
            assert report["status"] == "failed", (name, mode, report)
            assert d.actions() == trace, (name, "mutated despite failed observation")
        if restore:
            d.exec("python3", "-c", restore)
        else:
            d.exec("rm", "/etc/helix-demo/app.conf")
        d.apply(inputs, expected="unchanged" if name == "symlink ancestor" else "changed")


def file_failures(d):
    inputs = desired()
    d.apply(inputs)
    for mode in ("replace-failure", "after-replace"):
        changed = {**inputs, "files": {**inputs["files"], "/etc/helix-demo/app.conf": {
            **inputs["files"]["/etc/helix-demo/app.conf"], "content": mode + "\n"}}}
        before, trace = d.probe(), d.actions()
        review = d.controller("check", changed)
        d.fault({"file:/etc/helix-demo/app.conf": mode})
        report = d.controller("apply", changed, review)
        d.fault()
        failures = resource_failure(report, review, "file:/etc/helix-demo/app.conf", kind="linux_host")
        assert failures["file:/etc/helix-demo/app.conf"]["error"]["data"]["host_error"]["mutation_may_have_occurred"] is True
        assert d.actions() == trace, "failed replacement was logged as completed"
        assert d.exec("find", "/etc/helix-demo", "-name", ".helix-*").stdout == "", "staging file leaked"
        if mode == "replace-failure":
            after = d.probe()
            # Creating/unlinking the staging name legitimately updates its
            # parent's mtime. The live inode and every other observed field
            # must remain unchanged; allow only that directory bookkeeping.
            expected = {**before, "paths": {**before["paths"], "/etc/helix-demo": {
                **before["paths"]["/etc/helix-demo"], "mtime_ns": after["paths"]["/etc/helix-demo"]["mtime_ns"]}}}
            assert after == expected, ("failed publication changed live state", before, after)
            d.apply(changed)
        else:
            d.converged(changed)
            d.apply(changed, expected="unchanged")
        inputs = changed


def contention(d):
    inputs = desired()
    d.apply(inputs)
    d.exec("apt-get", "-y", "remove", "jq")
    pid = d.fixture_process("import fcntl,time; f=open('/var/lib/dpkg/lock-frontend','w'); fcntl.lockf(f,fcntl.LOCK_EX); open('/run/helix-linux/dpkg-locked','w').write('ready'); time.sleep(120)")
    try:
        d.wait_path("/run/helix-linux/dpkg-locked")
        review = d.controller("check", inputs)
        report = d.controller("apply", inputs, review)
        failures = resource_failure(report, review, "package:jq", kind="linux_host")
        assert "lock" in json.dumps(failures["package:jq"]).lower(), failures
        assert d.probe()["packages"]["jq"] is None
    finally:
        d.signal_process(pid, signal.SIGKILL)
    d.apply(inputs)
    conflict = {**inputs, "groups": {**inputs["groups"], "helix-extra": {"gid": 2001}}}
    review = d.controller("check", conflict)
    report = d.controller("apply", conflict, review)
    resource_failure(report, review, "group:helix-extra", kind="linux_host")
    assert d.probe()["groups"]["helix-extra"] is None
    d.apply(inputs, expected="unchanged")


def observations(d):
    source = """
from concurrent.futures import ThreadPoolExecutor
import json, pathlib, subprocess, threading
for repetition in range(4):
    pathlib.Path('/var/lib/helix-linux-host/target-id').unlink(missing_ok=True)
    barrier = threading.Barrier(12)
    def observe(_):
        barrier.wait(timeout=10)
        run = subprocess.run(['/usr/local/bin/helix-linux-host','observe','{\"subjects\":{}}'], capture_output=True, text=True, timeout=20)
        assert run.returncode == 0, run
        return json.loads(run.stdout)['target']
    with ThreadPoolExecutor(max_workers=12) as pool:
        values = list(pool.map(observe, range(12)))
    assert len(set(values)) == 1 and len(values[0]) == 32 and all(c in '0123456789abcdef' for c in values[0]), values
    assert pathlib.Path('/var/lib/helix-linux-host/target-id').read_text() == values[0]
assert not list(pathlib.Path('/var/lib/helix-linux-host').glob('.identity-*'))
print(json.dumps({'rounds':4,'readers':12,'status':'passed'}))
"""
    report = d.exec("python3", "-c", source)
    (d.artifacts / "concurrent-observations.json").write_text(report.stdout)
    assert d.actions() == []


def launch_controller(d, args, request, base):
    """One observable background-controller lifecycle for pause/crash scenarios."""
    d.exec("mkdir", "-p", base)
    d.put(base + "/request.json", json.dumps(request))
    launcher = ("import json,pathlib,subprocess,sys; base=pathlib.Path(sys.argv[1]); "
                "p=subprocess.Popen(json.loads(sys.argv[2]),stdin=open(base/'request.json'),"
                "stdout=open(base/'controller.stdout','w'),stderr=open(base/'controller.stderr','w'),start_new_session=True); "
                "(base/'controller.pid').write_text(str(p.pid)); (base/'controller.exit').write_text(str(p.wait()))")
    d.command("exec", "-d", d.container, "python3", "-c", launcher, base, json.dumps(args))
    d.wait_path(base + "/controller.pid")
    return int(d.exec("cat", base + "/controller.pid").stdout)


def late_drift(d):
    inputs = desired()
    d.apply(inputs)
    changed = {**inputs, "files": {
        "/etc/helix-demo/app.conf": {**inputs["files"]["/etc/helix-demo/app.conf"], "content": "first update\n"},
        "/srv/helix/app/README": {**inputs["files"]["/srv/helix/app/README"], "content": "second update\n",
                                  "after": ["file:/etc/helix-demo/app.conf"]}}}
    review = d.controller("check", changed)
    d.fault({"file:/srv/helix/app/README": "delay"})
    base = "/run/helix-linux/late-drift"
    launch_controller(d, d.controller_args("apply", changed, review),
                      {"mode": "apply", "config": changed, "review": review}, base)
    d.wait_path("/run/helix-linux/fault-reached.json")
    assert d.probe()["paths"]["/etc/helix-demo/app.conf"]["content"] == "first update\n"
    d.exec("python3", "-c", "from pathlib import Path; Path('/etc/helix-demo/app.conf').write_text('late drift\\n')")
    d.exec("touch", "/run/helix-linux/fault-release")
    d.wait_path(base + "/controller.exit")
    result = subprocess.CompletedProcess([], int(d.exec("cat", base + "/controller.exit").stdout),
                                         d.exec("cat", base + "/controller.stdout").stdout,
                                         d.exec("cat", base + "/controller.stderr").stdout)
    report = controller_report(result)
    assert_failure(report, "provider_postcondition", "verify")
    assert report["applied"] is True and d.probe()["paths"]["/etc/helix-demo/app.conf"]["content"] == "late drift\n"
    d.fault()
    since = len(d.actions())
    d.apply(changed)
    assert d.performed(since) == [("file:/etc/helix-demo/app.conf", "set")]


def sequences(d):
    for seed in (7, 41, 2026):
        d.fresh()
        rng = random.Random(seed)
        inputs = desired()
        d.apply(inputs)
        for step in range(12):
            content = f"seed={seed} step={step} token={rng.randrange(1000000)}\n"
            inputs = {**inputs, "files": {**inputs["files"], "/etc/helix-demo/app.conf": {
                "content": content, "group": "helix-app", "mode": rng.choice([0o600, 0o640, 0o644])}},
                "symlinks": {"/etc/helix-demo/current": {"target": rng.choice(["/srv/helix", "/srv/helix/app"]),
                                                       "state": rng.choice(["present", "absent"])}}}
            review = d.controller("check", inputs)
            if step % 3 == 0:
                d.exec("chmod", "777", "/srv/helix/app/README")
                assert_failure(d.controller("apply", inputs, review), "provider_stale_plan", "preflight")
                review = d.controller("check", inputs)
            d.apply(inputs, review=review)
            trace = d.actions()
            d.apply(inputs, expected="unchanged")
            assert d.actions() == trace, (seed, step, "non-idempotent state transition")


@contextmanager
def peer_pair(d):
    """Bootstrap transport only; each Helix machine plans/applies its peer's policy."""
    first = dict(container=d.container, sentinel=d.sentinel, unmanaged=d.unmanaged)
    d.container = None
    try:
        d.fresh(name=first["container"] + "-peer")
        second = dict(container=d.container, sentinel=d.sentinel, unmanaged=d.unmanaged)
        nodes = (first, second)
        for node in nodes:
            d.container = node["container"]
            d.exec("mkdir", "-p", "/run/sshd", "/run/helix-peer")
            for key in ("identity", "host"):
                d.exec("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", f"/run/helix-peer/{key}")
                node[key] = d.exec("cat", f"/run/helix-peer/{key}.pub").stdout.strip()
        for node, remote in ((first, second), (second, first)):
            d.container = node["container"]
            d.put("/run/helix-peer/authorized_keys", remote["identity"] + "\n")
            d.put("/run/helix-peer/known_hosts", f'{remote["container"]} {remote["host"]}\n')
            d.exec("chown", "-R", "0:0", "/run/helix-peer")
            d.exec("chmod", "700", "/run/helix-peer")
            d.exec("chmod", "600", "/run/helix-peer/authorized_keys", "/run/helix-peer/known_hosts")
            d.exec("usermod", "-p", "x", "root")
            d.put("/run/helix-peer/sshd_config", "\n".join([
                "Port 22", "ListenAddress 0.0.0.0", "HostKey /run/helix-peer/host",
                "PidFile /run/helix-peer/sshd.pid", "AuthorizedKeysFile /run/helix-peer/authorized_keys",
                "PermitRootLogin prohibit-password", "PasswordAuthentication no",
                "KbdInteractiveAuthentication no", "PubkeyAuthentication yes", "UsePAM no",
                "StrictModes yes", "AllowUsers root", "AllowTcpForwarding no", "X11Forwarding no", ""]))
            d.command("exec", "-d", d.container, "/usr/sbin/sshd", "-D", "-E", "/run/helix-peer/sshd.log",
                      "-f", "/run/helix-peer/sshd_config")
            d.wait_path("/run/helix-peer/sshd.pid")

        yield nodes
    finally:
        # Both nodes share the run label, so suite cleanup also covers partial setup.
        d.container, d.sentinel, d.unmanaged = first["container"], first["sentinel"], first["unmanaged"]
        keep = (os.environ.get("HELIX_DOCKER_LINUX_KEEP_PEERS") == "1" or
                sys.exc_info()[0] is not None and os.environ.get("HELIX_DOCKER_LINUX_KEEP_FAILED") == "1")
        if not keep:
            d.command("rm", "-f", first["container"] + "-peer", check=False)


def select_peer(d, node):
    d.container, d.sentinel, d.unmanaged = node["container"], node["sentinel"], node["unmanaged"]


def peer_args(target, *, grant=True, timeout_ms=120000, extra=(), program=PROGRAM):
    flags = ["helix", "perform", "--allow-stdin", "--allow-stdout", "--allow-read", "/opt/helix-programs",
             "--command-timeout-ms", str(timeout_ms)]
    if grant:
        flags += ["--allow-ssh", f'peer,host={target["container"]},user=root,'
                  'known_hosts=/run/helix-peer/known_hosts,identity=/run/helix-peer/identity']
    return [*flags, *extra, *([program] if program else [])]


def peer_run(d, source, target, mode=None, inputs=None, review=None, **options):
    request = {"mode": mode, "config": {**(inputs or desired()), "ssh_target": "peer"}}
    if review is not None:
        request["review"] = review
    result = d.command("exec", "-i", source["container"], *peer_args(target, **options),
                       input_text=json.dumps(request), timeout=300, check=False)
    select_peer(d, target)
    return result


def peer_converge(d, source, target, status):
    report = controller_report(peer_run(d, source, target, program="/opt/helix-programs/docker_linux/peer.yaml"))
    assert report["status"] == status, report
    d.converged(desired())
    return report


def peers(d):
    with peer_pair(d) as nodes:
        first, second = nodes
        for source, target in ((first, second), (second, first)):
            peer_converge(d, source, target, "changed")
            before = d.actions()
            peer_converge(d, source, target, "unchanged")
            assert d.actions() == before, "converged peer performed mutations"
            d.exec("sh", "-c", "printf 'peer drift\\n' > /etc/helix-demo/app.conf")
            peer_converge(d, source, target, "changed")
            assert d.performed(len(before)) == [("file:/etc/helix-demo/app.conf", "set")], d.actions()[len(before):]
            peer_converge(d, source, target, "unchanged")
        (d.artifacts / "peers.json").write_text(json.dumps({"controllers": [n["container"] for n in nodes],
            "transport": "container-to-container SSH with pinned host keys", "status": "passed"}, indent=2) + "\n")


def peer_failures(d):
    with peer_pair(d) as (first, second):
        for source, target in ((first, second), (second, first)):
            peer_converge(d, source, target, "changed")
            state, trace = d.probe(), d.actions()
            denied = controller_report(peer_run(d, source, target, "check", grant=False))
            assert denied["status"] == "failed" and "capability" in json.dumps(denied), denied
            select_peer(d, source)
            known = d.exec("cat", "/run/helix-peer/known_hosts").stdout
            d.exec("truncate", "-s", "0", "/run/helix-peer/known_hosts")
            failed = controller_report(peer_run(d, source, target, "check"))
            assert failed["status"] == "failed" and "ssh_host_key" in json.dumps(failed), failed
            select_peer(d, source)
            d.put("/run/helix-peer/known_hosts", known)
            select_peer(d, target)
            authorized = d.exec("cat", "/run/helix-peer/authorized_keys").stdout
            d.exec("truncate", "-s", "0", "/run/helix-peer/authorized_keys")
            failed = controller_report(peer_run(d, source, target, "check"))
            assert failed["status"] == "failed" and "ssh_auth" in json.dumps(failed), failed
            d.put("/run/helix-peer/authorized_keys", authorized)
            d.exec("chown", "0:0", "/run/helix-peer/authorized_keys")
            assert d.probe() == state and d.actions() == trace, "transport rejection mutated target"

            changed = {**desired(), "files": {**desired()["files"], "/etc/helix-demo/app.conf": {
                **desired()["files"]["/etc/helix-demo/app.conf"], "content": "remote update\n"}}}
            review = controller_report(peer_run(d, source, target, "check", changed))
            d.exec("chmod", "600", "/srv/helix/app/README")
            before, trace = d.probe(), d.actions()
            assert_failure(controller_report(peer_run(d, source, target, "apply", changed, review)),
                           "provider_stale_plan", "preflight")
            assert d.probe() == before and d.actions() == trace
            peer_converge(d, source, target, "changed")

            review = controller_report(peer_run(d, source, target, "check", changed))
            before, trace = d.probe(), d.actions()
            d.fault({"file:/etc/helix-demo/app.conf": "delay"})
            failed = controller_report(peer_run(d, source, target, "apply", changed, review, timeout_ms=1000))
            assert failed["status"] == "failed" and "timeout" in json.dumps(failed), failed
            # Remote command uncertainty is not remote process fencing. Terminate
            # the fixture-owned paused helper before taking a fresh review.
            paused = json.loads(d.exec("cat", "/run/helix-linux/fault-reached.json").stdout)
            d.exec("python3", "-c", "import os,signal,sys\ntry: os.kill(int(sys.argv[1]),signal.SIGKILL)\nexcept ProcessLookupError: pass", str(paused["pid"]))
            d.fault()
            assert d.probe() == before and d.actions() == trace, "pre-mutation timeout changed target"

            review = controller_report(peer_run(d, source, target, "check", changed))
            d.fault({"file:/etc/helix-demo/app.conf": "after-failure"})
            failed = controller_report(peer_run(d, source, target, "apply", changed, review))
            d.fault()
            assert failed["status"] == "failed", failed
            d.converged(changed)  # Failure must not conceal already visible mutation.
            assert controller_report(peer_run(d, source, target, "check", changed))["status"] == "unchanged"
            peer_converge(d, source, target, "changed")
            peer_converge(d, source, target, "unchanged")


def peer_recovery(d):
    import yaml
    with peer_pair(d) as (source, target):
        peer_converge(d, source, target, "changed")
        changed = {**desired(), "files": {**desired()["files"], "/etc/helix-demo/app.conf": {
            **desired()["files"]["/etc/helix-demo/app.conf"], "content": "after durable recovery\n"}}}
        review = controller_report(peer_run(d, source, target, "check", changed))
        d.fault({"file:/etc/helix-demo/app.conf": "after-mutation"})
        select_peer(d, source)
        request = {"mode": "apply", "config": {**changed, "ssh_target": "peer"}, "review": review}
        checkpoint = "/run/helix-peer/execution.json"
        args = peer_args(target, extra=("--checkpoint", checkpoint))
        pid = launch_controller(d, args, request, "/run/helix-peer")
        select_peer(d, target)
        d.wait_path("/run/helix-linux/fault-reached.json")
        paused = json.loads(d.exec("cat", "/run/helix-linux/fault-reached.json").stdout)
        assert paused["phase"] == "after", paused
        d.converged(changed)
        trace = d.actions()
        select_peer(d, source)
        inspected = d.exec("helix", "checkpoint", checkpoint)
        pending = yaml.safe_load(inspected.stdout)["pending_effect"]
        assert pending["operation"] == "ssh_command" and pending["payload"]["args"][0] == "apply", pending
        d.signal_process(pid, signal.SIGKILL)
        refused = peer_run(d, source, target, extra=("--resume-checkpoint", checkpoint), program=None)
        assert refused.returncode != 0 and "uncertain" in refused.stderr, refused
        assert d.actions() == trace, "uncertain remote mutation replayed"
        d.exec("python3", "-c", "import os,signal,sys\ntry: os.kill(int(sys.argv[1]),signal.SIGKILL)\nexcept ProcessLookupError: pass", str(paused["pid"]))
        d.fault()
        # Target container restart preserves bytes but loses transport processes.
        # Restart the fixture transport explicitly, then independently recheck
        # the mutation before acknowledging the source controller's uncertainty.
        d.command("restart", target["container"])
        d.command("exec", "-d", target["container"], "sleep", "86400")
        target["unmanaged"] = d.probe()["processes"]
        select_peer(d, target)
        d.exec("rm", "-f", "/run/helix-peer/sshd.pid")
        d.command("exec", "-d", target["container"], "/usr/sbin/sshd", "-D", "-E", "/run/helix-peer/sshd.log",
                  "-f", "/run/helix-peer/sshd_config")
        d.wait_path("/run/helix-peer/sshd.pid")
        d.converged(changed)
        outcome = {"request": {key: pending[key] for key in ("id", "operation", "payload")},
                   "outcome": {"ok": {"code": 0, "stdout": json.dumps({"completed": True, "resource": "file:/etc/helix-demo/app.conf"}), "stderr": ""}}}
        select_peer(d, source)
        wrong = json.loads(json.dumps(outcome))
        wrong["request"]["id"] += 1
        d.put("/run/helix-peer/outcome.json", json.dumps(wrong))
        rejected = d.exec("helix", "reconcile", checkpoint, "/run/helix-peer/outcome.json", check=False)
        assert rejected.returncode != 0, "mismatched reconciliation accepted"
        d.put("/run/helix-peer/outcome.json", json.dumps(outcome))
        d.exec("helix", "reconcile", checkpoint, "/run/helix-peer/outcome.json")
        denied_checkpoint = "/run/helix-peer/denied.json"
        d.exec("cp", checkpoint, denied_checkpoint)
        denied = peer_run(d, source, target, grant=False, extra=("--resume-checkpoint", denied_checkpoint), program=None)
        assert denied.returncode != 0 and "capability" in denied.stderr, denied
        # A denied branch must not consume the continuation used with fresh grants.
        resumed = peer_run(d, source, target, extra=("--resume-checkpoint", checkpoint), program=None)
        report = controller_report(resumed)
        assert report["status"] == "changed", report
        d.converged(changed)
        assert d.actions() == trace, "acknowledged mutation repeated after restore"
        select_peer(d, source)
        # Complete journals contain source/object tables and can exceed the
        # bounded command-output channel. Copy the artifact without rendering it.
        d.command("cp", f'{source["container"]}:{checkpoint}', str(d.artifacts / "peer-recovery.json"))
        repeated = peer_run(d, source, target, extra=("--resume-checkpoint", checkpoint), program=None)
        assert repeated.returncode == 0 and repeated.stdout == "" and yaml.safe_load(repeated.stderr)["root"] == report
        assert d.actions() == trace


def peer_parallel(d):
    with peer_pair(d) as nodes:
        for repetition, expected in enumerate(("changed", "unchanged", "changed")):
            before = {}
            for node in nodes:
                select_peer(d, node)
                before[node["container"]] = len(d.actions())
                if repetition == 2:
                    d.exec("python3", "-c", "from pathlib import Path; Path('/etc/helix-demo/app.conf').write_text('parallel drift\\n')")
            for source, target in ((nodes[0], nodes[1]), (nodes[1], nodes[0])):
                select_peer(d, source)
                base = f"/run/helix-peer/parallel-{repetition}"
                launch_controller(d, peer_args(target, program="/opt/helix-programs/docker_linux/peer.yaml"), {}, base)
            for node in nodes:
                select_peer(d, node)
                base = f"/run/helix-peer/parallel-{repetition}"
                d.wait_path(base + "/controller.exit")
                result = subprocess.CompletedProcess([], int(d.exec("cat", base + "/controller.exit").stdout),
                                                     d.exec("cat", base + "/controller.stdout").stdout,
                                                     d.exec("cat", base + "/controller.stderr").stdout)
                assert controller_report(result)["status"] == expected, result
                d.converged(desired())
                performed = d.performed(before[node["container"]])
                if repetition == 1:
                    assert performed == [], performed
                elif repetition == 2:
                    assert performed == [("file:/etc/helix-demo/app.conf", "set")], performed


def verify(h):
    names, repeats = options()
    d = LinuxDocker(h.root)
    d.artifacts = h.root / "runtime" / "docker-linux" / d.run_id
    d.artifacts.mkdir(parents=True, exist_ok=True)
    d.image = f"helix-docker-linux:{d.run_id}"
    d.network = f"helix-linux-{d.run_id}"
    failed, results = True, []
    previous = signal.getsignal(signal.SIGTERM)

    def interrupted(signum, frame):
        raise KeyboardInterrupt("Docker Linux suite cancelled")
    signal.signal(signal.SIGTERM, interrupted)
    print(f"docker_linux artifacts: {d.artifacts.relative_to(h.root)}")
    try:
        d.progress(phase="provision", status="running")
        d.provision()
        for repetition in range(repeats):
            for name in names:
                d.progress(phase="scenario", status="running", scenario=name, repetition=repetition + 1, completed=results)
                d.fresh()
                start = time.monotonic()
                d.scenario_deadline = start + 900
                try:
                    globals()[name](d)
                except BaseException as error:
                    results.append(dict(scenario=name, repetition=repetition + 1, status="failed", error=str(error), replay=replay(name)))
                    raise AssertionError(f"{name}: {error}\nreplay: {replay(name)}\nartifacts: {d.artifacts}") from error
                results.append(dict(scenario=name, repetition=repetition + 1, status="passed", elapsed_ms=round((time.monotonic() - start) * 1000), replay=replay(name)))
                d.scenario_deadline = None
                print(f"docker_linux {name} repetition={repetition + 1}: passed")
        failed = False
    finally:
        signal.signal(signal.SIGTERM, previous)
        active_failure = sys.exc_info()[0] is not None
        errors = []
        retained = (failed and os.environ.get("HELIX_DOCKER_LINUX_KEEP_FAILED") == "1" or
                    not failed and os.environ.get("HELIX_DOCKER_LINUX_KEEP_PEERS") == "1")
        d.cleaning = True
        if d.container:
            try:
                d.command("cp", f"{d.container}:{TRACE}", str(d.artifacts / "actions.jsonl"), timeout=15, check=False)
            except Exception as error:
                errors.append(str(error))
        if not retained and d.daemon_ready:
            try:
                d.cleanup()
            except Exception as error:
                errors.append(str(error))
        report = dict(version=1, run_id=d.run_id, status="failed" if failed or errors else "passed", retained=retained,
                      elapsed_ms=round((time.monotonic() - d.started) * 1000), versions=VERSIONS, packages=PACKAGES,
                      scenarios=results, commands=d.events, cleanup_errors=errors,
                      cleanup=f"./manage.py docker-nginx cleanup {d.run_id}")
        (d.artifacts / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        if retained:
            print(f"docker_linux retained containers: run_id={d.run_id}; cleanup: {report['cleanup']}")
        d.progress(phase="complete", status=report["status"], completed=results)
        if errors and not active_failure:
            raise AssertionError("Docker evidence/cleanup failed: " + "; ".join(errors))


def verify_harness(h):
    """Exercise selection, oracle and fixture boundaries without Docker infrastructure."""
    from unittest.mock import patch
    import yaml
    import shutil
    import importlib.util
    import tempfile
    fixture = h.root / "tests/fixtures/docker_linux"
    compile((fixture / "host.py").read_text(), str(fixture / "host.py"), "exec")
    compile(PROBE, "docker_linux independent probe", "exec")
    dockerfile = (fixture / "Dockerfile").read_text()
    validate_versions()
    for name in ("HELIX_BUILDER_IMAGE", "HELIX_RUNTIME_IMAGE", "HELIX_PACKAGE_SNAPSHOT"):
        assert f"ARG {name}\n" in dockerfile
    assert all(callable(globals().get(name)) for name in SCENARIOS)
    cases = adversarial_configurations()
    expression = {"$call": ["let", {"linux": {"$call": ["import", "std:linux"]}}, {
        name: {"$call": ["try", {"$call": ["linux.resolve", {"$literal": config}]},
                         {"$call": ["fn", ["error"], {"$ref": "error.kind"}]}]}
        for name, config in cases.items()}]}
    result = h.helix("run", "-", stdin=json.dumps(expression))
    assert result.returncode == 0, result
    assert yaml.safe_load(result.stdout)["root"] == {name: declaration_error(name) for name in cases}
    # Planning must expose symbolic ownership rebinding, without propagating a
    # non-identity account change to otherwise unchanged files.
    config = {"groups": {"helix-app": {"gid": 2002}},
              "users": {"helix-app": {"uid": 2003, "group": "helix-app", "home": "/home/helix-app"}},
              "files": {"/etc/helix-demo/app.conf": {"content": "x", "owner": "helix-app", "group": "helix-app"},
                        "/etc/helix-demo/extra.conf": {"content": "keep"}}}
    before = {"target": "fixture", "resources": {
        "group:helix-app": {"gid": 2001},
        "user:helix-app": {"uid": 2001, "group": "helix-app", "home": "/home/helix-app", "shell": "/usr/sbin/nologin"},
        "file:/etc/helix-demo/app.conf": {"content": "x", "owner": "helix-app", "group": "helix-app", "mode": 420},
        "file:/etc/helix-demo/extra.conf": {"content": "keep", "owner": "root", "group": "root", "mode": 420}}}
    shell = {**config, "groups": {"helix-app": {"gid": 2001}},
             "users": {"helix-app": {**config["users"]["helix-app"], "uid": 2001, "shell": "/bin/sh"}}}
    expression = {"$call": ["let", {"linux": {"$call": ["import", "std:linux"]}}, {
        name: {"$call": ["linux.plan", {"$literal": value}, {"$literal": before}]}
        for name, value in (("identity", config), ("shell", shell))}]}
    result = h.helix("run", "-", stdin=json.dumps(expression))
    assert result.returncode == 0, result
    plans = yaml.safe_load(result.stdout)["root"]
    assert [step["field"] for step in plans["identity"]["steps"]] == ["file:/etc/helix-demo/app.conf", "group:helix-app", "user:helix-app"], plans
    assert plans["identity"]["steps"][0]["from"] == plans["identity"]["steps"][0]["to"]
    assert [step["field"] for step in plans["shell"]["steps"]] == ["user:helix-app"], plans
    with patch.dict(os.environ, {"HELIX_DOCKER_LINUX_SCENARIO": "baseline", "HELIX_DOCKER_LINUX_REPEAT": "1"}):
        assert options() == (["baseline"], 1)
    for name, value in (("HELIX_DOCKER_LINUX_SCENARIO", "unknown"), ("HELIX_DOCKER_LINUX_REPEAT", "0")):
        with patch.dict(os.environ, {name: value}):
            try:
                options()
            except ValueError:
                pass
            else:
                raise AssertionError("invalid Docker Linux selector accepted")
    for selected, repeat, keep, valid in (("peers", "1", "1", True), ("baseline", "1", "1", False),
                                         ("peers", "2", "1", False), ("peers", "1", "yes", False)):
        with patch.dict(os.environ, {"HELIX_DOCKER_LINUX_SCENARIO": selected,
                                     "HELIX_DOCKER_LINUX_REPEAT": repeat,
                                     "HELIX_DOCKER_LINUX_KEEP_PEERS": keep}):
            try:
                options()
            except ValueError:
                assert not valid, "valid peer retention rejected"
            else:
                assert valid, "invalid peer retention accepted"
    # The dependency oracle follows transitive requirements and ignores unrelated resources.
    review = {"dependencies": {"resources": {"a": {"requires": []}, "b": {"requires": ["a"]}, "c": {"requires": ["b"]}, "d": {"requires": []}}}}
    assert dependents(review, "a") == {"b", "c"} and dependents(review, "d") == set()
    # The independent oracle rejects each corruption class even when a report would claim success.
    d = LinuxDocker(h.root)
    try:
        inputs = desired()
        groups, users, paths, packages = expected(inputs)
        good = dict(packages={"jq": packages["jq"], "tree": packages["tree"]},
                    groups={"helix-app": 2001, "helix-extra": None},
                    users={"helix-app": dict(uid=2001, gid=2001, home="/srv/helix/app", shell="/usr/sbin/nologin"), "helix-extra": None},
                    paths={path: None for path in WATCHED["paths"]}, processes={})
        owners = {"root": 0, "helix-app": 2001}
        for path, want in paths.items():
            if want["type"] == "symlink":
                good["paths"][path] = dict(type="symlink", target=want["target"])
            else:
                good["paths"][path] = dict(type=want["type"], uid=owners[want["owner"]], gid=owners[want["group"]], mode=want["mode"], **({"content": want["content"]} if want["type"] == "file" else {}))
        good["paths"][SENTINEL] = d.sentinel = dict(type="file", content="x")
        d.unmanaged = {}
        with patch.object(d, "probe", return_value=good):
            d.converged(inputs)
        def damaged(path, **change):
            return {**good, "paths": {**good["paths"], path: {**good["paths"][path], **change}}}
        for corrupt in ({**good, "packages": {**good["packages"], "jq": None}},
                       {**good, "groups": {**good["groups"], "helix-extra": 9}},
                       {**good, "users": {**good["users"], "helix-app": None}},
                       damaged("/etc/helix-demo/app.conf", mode=0o666), damaged("/etc/helix-demo/app.conf", content="drift"),
                       damaged("/etc/helix-demo/current", target="/tmp"), damaged("/srv/helix", uid=1)):
            with patch.object(d, "probe", return_value=corrupt):
                try:
                    d.converged(inputs)
                except AssertionError:
                    continue
                raise AssertionError("independent observation accepted corrupt actual state")
    finally:
        shutil.rmtree(d.artifacts)
    # Real helper mechanisms run without Docker in project-local scratch space;
    # root/user/package mutations remain exclusive to the opt-in container suite.
    spec = importlib.util.spec_from_file_location("helix_linux_fixture", fixture / "host.py")
    host = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(host)
    with tempfile.TemporaryDirectory(dir=h.root / "runtime", prefix="linux-helper-") as directory:
        base = Path(directory)
        host.ROOTS, host.RUN, host.DATA = (base,), base / "run", base / "data"
        path = base / "bytes"
        for content in (b"a\r\nb\r", "λ 😀".encode(), b"a\0b", b""):
            path.write_bytes(content)
            assert host.observe_file(str(path))["content"].encode() == content
        assert len(host.target()) == 32 and host.target() == host.target()
        assert host.command([sys.executable, "-c", "print('bounded')"]) == "bounded\n"
        host.LIMIT = 1024
        for script, timeout, message in (("print('x'*2048)", 5, "output limit"),
                                         ("import time; time.sleep(5)", 0.05, "timed out")):
            try:
                host.command([sys.executable, "-c", script], timeout=timeout)
            except (RuntimeError, subprocess.TimeoutExpired) as error:
                assert message in str(error), error
            else:
                raise AssertionError("helper mechanism escaped its bound")
