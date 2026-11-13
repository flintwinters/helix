"""Opt-in real Docker acceptance; Python observes, Helix decides desired state."""
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid


LABEL = "org.helix.docker-nginx.run"
VERSIONS = json.loads((Path(__file__).resolve().parents[1] / "fixtures/docker_nginx/versions.json").read_text())
PACKAGE_VERSION = VERSIONS["nginx_version"]
HOST = "/usr/local/bin/helix-nginx-host"
FILES = ("/etc/helix-nginx/nginx.conf", "/etc/helix-nginx/server.conf", "/var/lib/helix-nginx/index.html")
TRACE = "/var/lib/helix-nginx-host/actions.jsonl"
FAULT = "/run/helix-nginx/fault.json"
SCENARIOS = {
    "baseline": "Clean preview, reviewed apply, independent state and two no-op cycles",
    "updates": "Coalesced configuration reload and content-only update",
    "drift": "Independent bytes, metadata, missing file, process and package drift",
    "combined_drift": "Missing package/files and stopped service converge together",
    "invalid_config": "Invalid nginx candidate preserves the live response",
    "invalid_input": "Types and unsupported settings fail before mutation",
    "stale_review": "Observed state, desired inputs and target identity invalidate review",
    "authority": "Missing command authority and failed observations fail closed",
    "package_failure": "Unavailable version, repository failure and lock contention",
    "file_failure": "Permission and replacement failures preserve unaffected state",
    "service_failure": "Occupied port and injected start/reload failures are bounded",
    "false_success": "Successful command without mutation fails postcondition checking",
    "cancellation": "Timeout/cancellation stop further resource mutations",
    "controller_death": "Fresh reconciliation after unacknowledged mutation or inactive files",
    "removal": "Explicit owned removal preserves unrelated state and becomes a no-op",
    "lifecycle": "Container restart/recreation and fresh target identity",
    "sensitivity": "Independent oracles reject suppressed reload and false no-op claims",
}

# Deliberately independent of adapter observation/planning code. Read actual OS state.
PROBE = r'''
import hashlib, json, os, pathlib, subprocess, sys, urllib.request
paths = ['/etc/helix-nginx/nginx.conf', '/etc/helix-nginx/server.conf', '/var/lib/helix-nginx/index.html', '/var/lib/helix-unmanaged/sentinel']
files = {}
for name in paths:
    p = pathlib.Path(name)
    if p.exists():
        s = p.stat()
        files[name] = dict(content=p.read_text(), uid=s.st_uid, gid=s.st_gid, mode=s.st_mode & 0o7777, inode=s.st_ino, mtime_ns=s.st_mtime_ns)
    else:
        files[name] = None
package = subprocess.run(['dpkg-query','-W','-f=${db:Status-Status} ${Version}','nginx'], text=True, capture_output=True, timeout=5)
processes = {}
for p in pathlib.Path('/proc').iterdir():
    if p.name.isdigit():
        try:
            command = (p/'cmdline').read_bytes().replace(b'\0',b' ').decode(errors='replace')
            if command.startswith('nginx: master') or command.startswith('sleep 86400'):
                processes[p.name] = command
        except (OSError, UnicodeError):
            pass
try:
    with urllib.request.urlopen('http://127.0.0.1:'+sys.argv[1]+'/', timeout=2) as response:
        http = dict(status=response.status, body=response.read(1048577).decode(), server=response.headers.get('Server'), activation=response.headers.get('X-Helix-Activation'))
except Exception:
    http = None
print(json.dumps(dict(package=package.stdout if package.returncode == 0 and package.stdout.startswith('installed ') else None, files=files, processes=processes, http=http)))
'''


def validate_versions():
    for field in ("image", "builder"):
        if not re.fullmatch(r"[^\s]+@sha256:[a-f0-9]{64}", VERSIONS[field]):
            raise ValueError(f"fixture {field} must be pinned by a full SHA256 image digest")
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", VERSIONS["package_snapshot"]):
        raise ValueError("fixture package snapshot must be an explicit Debian snapshot timestamp")
    if VERSIONS["platform"] != "linux/amd64" or not PACKAGE_VERSION:
        raise ValueError("fixture qualifies Linux/amd64 and requires an explicit nginx version")


def replay(name):
    return f"HELIX_DOCKER_NGINX_SCENARIO={name} ./manage.py test --suite docker_nginx"


def options():
    chosen = os.environ.get("HELIX_DOCKER_NGINX_SCENARIO", "all")
    if chosen != "all" and chosen not in SCENARIOS:
        raise ValueError(f"unknown Docker nginx scenario {chosen!r}; ./manage.py docker-nginx describe")
    repeat = int(os.environ.get("HELIX_DOCKER_NGINX_REPEAT", "2"))
    if not 1 <= repeat <= 10:
        raise ValueError("HELIX_DOCKER_NGINX_REPEAT must be between 1 and 10")
    return list(SCENARIOS) if chosen == "all" else [chosen], repeat


class Docker:
    """Bounded Docker subprocesses with retained output and owned-resource cleanup."""

    def __init__(self, root, run_id=None):
        self.root = root
        self.run_id = run_id or uuid.uuid4().hex
        if not re.fullmatch(r"[a-f0-9]{32}", self.run_id):
            raise ValueError("run id must be the 32 lowercase hex characters in the report")
        self.artifacts = root / "runtime" / "docker-nginx" / self.run_id
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.serial = 0
        self.container = None
        self.image = f"helix-docker-nginx:{self.run_id}"
        self.network = f"helix-nginx-{self.run_id}"
        self.started = time.monotonic()
        self.deadline = self.started + 7200
        self.scenario_deadline = None
        self.events = []
        self.daemon_ready = False
        self.cleaning = False

    def command(self, *args, timeout=60, check=True, input_text=None):
        deadline = min(self.deadline, self.scenario_deadline or self.deadline)
        if not self.cleaning and time.monotonic() >= deadline:
            raise AssertionError("Docker nginx exceeded scenario (15min) or suite (2h) bound")
        if not self.cleaning:
            timeout = min(timeout, max(0.1, deadline - time.monotonic()))
        self.serial += 1
        prefix = self.artifacts / f"command-{self.serial:04d}"
        command = ["docker", *map(str, args)]
        prefix.with_suffix(".stdin").write_text(input_text or "")
        with prefix.with_suffix(".stdin").open() as source, prefix.with_suffix(".stdout").open("w+") as out, prefix.with_suffix(".stderr").open("w+") as err:
            start = time.monotonic()
            try:
                process = subprocess.Popen(command, stdin=source, stdout=out, stderr=err, text=True, start_new_session=True)
            except FileNotFoundError as error:
                raise RuntimeError("Docker prerequisite unavailable: install Docker CLI and provide a reachable daemon; explicit docker_nginx acceptance cannot be skipped") from error
            try:
                while process.poll() is None:
                    if time.monotonic() - start > timeout or max(os.fstat(out.fileno()).st_size, os.fstat(err.fileno()).st_size) > 8 * 1024 * 1024:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=10)
                        raise AssertionError(f"Docker command exceeded time/output bound: {command}; artifacts={prefix}")
                    time.sleep(0.05)
            except BaseException:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)
                raise
            out.seek(0)
            err.seek(0)
            result = subprocess.CompletedProcess(command, process.returncode, out.read(), err.read())
        self.events.append(dict(command=command, code=result.returncode, elapsed_ms=round((time.monotonic()-start)*1000)))
        if check and result.returncode:
            raise AssertionError(f"Docker command failed ({result.returncode}): {command}\n{result.stdout}\n{result.stderr}")
        return result

    def provision(self):
        validate_versions()
        version = self.command("version", "--format", "{{json .}}", timeout=15)
        self.daemon_ready = True
        (self.artifacts / "docker-version.json").write_text(version.stdout)
        security = self.command("info", "--format", "{{json .SecurityOptions}}", timeout=15)
        (self.artifacts / "docker-security-options.json").write_text(security.stdout)
        context = self.artifacts / "context"
        context.mkdir()
        for name in ("Cargo.toml", "Cargo.lock"):
            shutil.copy2(self.root / name, context / name)
        for name in ("src", "std", "completions", "editor", "tests/programs", "tests/fixtures/docker_nginx"):
            shutil.copytree(self.root / name, context / name)
        self.command("build", "--force-rm", "--platform", VERSIONS["platform"], "--label", f"{LABEL}={self.run_id}",
                     "--build-arg", "HELIX_BUILDER_IMAGE=" + VERSIONS["builder"],
                     "--build-arg", "HELIX_RUNTIME_IMAGE=" + VERSIONS["image"],
                     "--build-arg", "HELIX_PACKAGE_SNAPSHOT=" + VERSIONS["package_snapshot"],
                     "-t", self.image, "-f", str(context / "tests/fixtures/docker_nginx/Dockerfile"), str(context), timeout=1800)
        image = self.command("image", "inspect", self.image)
        (self.artifacts / "image.json").write_text(image.stdout)
        self.command("network", "create", "--label", f"{LABEL}={self.run_id}", self.network)
        self.fresh()
        identity = self.exec("sha256sum", "/usr/local/bin/helix")
        (self.artifacts / "controller-sha256.txt").write_text(identity.stdout)

    def fresh(self):
        if self.container:
            self.command("rm", "-f", self.container)
        self.container = f"helix-nginx-{self.run_id}"
        self.command("run", "-d", "--name", self.container, "--label", f"{LABEL}={self.run_id}",
                     "--platform", VERSIONS["platform"], "--network", self.network, "--pids-limit", "256", "--memory", "512m",
                     "--publish", "127.0.0.1::8080", self.image)
        self.wait_supervisor()
        metadata = json.loads(self.command("inspect", self.container).stdout)[0]
        assert metadata["HostConfig"]["Privileged"] is False and metadata["HostConfig"]["PidMode"] != "host", metadata["HostConfig"]
        assert not metadata["Mounts"], "fixture must not mount host paths or Docker sockets"
        (self.artifacts / f"container-{self.serial:04d}.json").write_text(json.dumps(metadata, indent=2))
        self.exec("cat", "/proc/self/attr/current", check=False)
        self.exec("cat", "/proc/self/status")
        self.exec("python3", "-c", "from pathlib import Path; p=Path('/var/lib/helix-unmanaged'); p.mkdir(exist_ok=True); (p/'sentinel').write_text('unmanaged\\n')")
        self.command("exec", "-d", self.container, "sleep", "86400")
        self.sentinel = self.probe()["files"]["/var/lib/helix-unmanaged/sentinel"]
        self.unmanaged = {pid: value for pid, value in self.probe()["processes"].items() if value.startswith("sleep 86400")}
        assert self.unmanaged, "unmanaged sentinel process missing"

    def wait_supervisor(self):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            result = self.command("exec", self.container, HOST, "observe", '{"port":8080}', timeout=15, check=False)
            if result.returncode == 0:
                return
            time.sleep(0.1)
        raise AssertionError("fixture supervisor did not become ready")

    def exec(self, *args, **kwargs):
        return self.command("exec", self.container, *args, **kwargs)

    def put(self, name, value):
        self.serial += 1
        path = self.artifacts / f"input-{self.serial:04d}"
        path.write_text(value)
        self.command("cp", str(path), f"{self.container}:{name}")

    def fault(self, value=None):
        self.exec("rm", "-f", "/run/helix-nginx/fault-reached.json", "/run/helix-nginx/fault-release")
        if value is None:
            self.exec("rm", "-f", FAULT)
        else:
            self.put(FAULT, json.dumps(value))

    def host(self, operation, payload=None, **kwargs):
        return self.exec(HOST, operation, json.dumps(payload or {}), **kwargs)

    def probe(self, port=8080):
        state = json.loads(self.exec("python3", "-c", PROBE, str(port)).stdout)
        self.serial += 1
        (self.artifacts / f"observation-{self.serial:04d}.json").write_text(json.dumps(state, indent=2))
        return state

    def actions(self):
        result = self.exec("cat", TRACE, check=False)
        return [json.loads(line) for line in result.stdout.splitlines()] if result.returncode == 0 else []

    def controller_args(self, mode, inputs, review=None, *, grant=True, timeout_ms=120000):
        request = {"mode": mode, "config": inputs}
        if review is not None:
            request["before" if mode == "plan" else "review"] = review
        self.put("/run/helix-nginx/request.json", json.dumps(request))
        return ["helix", "perform", "--allow-stdin", "--allow-stdout", *(["--allow-command", HOST] if grant else []),
                "--command-timeout-ms", str(timeout_ms), "/opt/helix-programs/docker_nginx/operate.yaml"]

    def controller(self, mode, inputs, review=None, *, grant=True, timeout_ms=120000):
        import yaml
        args = self.controller_args(mode, inputs, review, grant=grant, timeout_ms=timeout_ms)
        request = {"mode": mode, "config": inputs, **({"before" if mode == "plan" else "review": review} if review is not None else {})}
        result = self.command("exec", "-i", self.container, *args, input_text=json.dumps(request), timeout=timeout_ms / 1000 + 15, check=False)
        state = yaml.safe_load(result.stderr)
        assert isinstance(state, dict) and state.get("status") in {"finished", "error", "signaled"}, result
        engine_code = 0 if state["status"] == "finished" else 1
        problem = __import__("tooling.test_runner", fromlist=["malformed_state"]).malformed_state(result.stderr, engine_code)
        assert problem is None, (problem, result.stdout, result.stderr)
        if state["status"] != "finished":
            assert result.returncode == 1, result
            return {"status": "failed", "error": state.get("error"), "engine_status": state["status"]}
        report = json.loads(result.stdout)
        assert report == state["root"], (report, state)
        expected_code = (1 if report.get("status") in {"failed", "invalid", "unsupported", "skipped"}
                         else 2 if report.get("status") == "changed" and not report.get("applied") else 0)
        assert result.returncode == expected_code, (result.returncode, report)
        return report

    def fixture_process(self, source):
        launcher = "import subprocess,sys; p=subprocess.Popen(['python3','-c',sys.argv[1]],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True); print(p.pid)"
        return int(self.exec("python3", "-c", launcher, source).stdout)

    def wait_path(self, path):
        for _ in range(100):
            if self.exec("test", "-f", path, check=False).returncode == 0:
                return
            time.sleep(0.05)
        raise AssertionError(f"fixture boundary not reached: {path}")

    def background(self, mode, inputs, review):
        args = self.controller_args(mode, inputs, review)
        self.exec("rm", "-f", "/run/helix-nginx/controller.pid", "/run/helix-nginx/controller.exit")
        launcher = "import json,pathlib,subprocess,sys; out=open('/run/helix-nginx/controller.stdout','w'); err=open('/run/helix-nginx/controller.stderr','w'); p=subprocess.Popen(json.loads(sys.argv[1]),stdin=open('/run/helix-nginx/request.json'),stdout=out,stderr=err,start_new_session=True); pathlib.Path('/run/helix-nginx/controller.pid').write_text(str(p.pid)); code=p.wait(); pathlib.Path('/run/helix-nginx/controller.exit').write_text(str(code))"
        self.command("exec", "-d", self.container, "python3", "-c", launcher, json.dumps(args))
        self.wait_path("/run/helix-nginx/controller.pid")
        return int(self.exec("cat", "/run/helix-nginx/controller.pid").stdout)

    def wait_fault(self):
        for _ in range(200):
            if self.exec("test", "-f", "/run/helix-nginx/fault-reached.json", check=False).returncode == 0:
                return
            time.sleep(0.05)
        raise AssertionError("fault injection boundary was not reached")

    def kill_controller(self, pid, signum):
        import yaml
        groups = []
        if signum == signal.SIGKILL:
            inspect_children = "import json,os,pathlib,sys; p=sys.argv[1]; children=pathlib.Path('/proc/'+p+'/task/'+p+'/children').read_text().split(); print(json.dumps(sorted({os.getpgid(int(child)) for child in children})))"
            groups = json.loads(self.exec("python3", "-c", inspect_children, str(pid)).stdout)
        self.exec("python3", "-c", "import os,signal,sys; os.kill(int(sys.argv[1]),int(sys.argv[2]))", str(pid), str(signum))
        if signum == signal.SIGKILL:
            # Fault injection owns these observed child groups. SIGKILL itself
            # promises no cleanup; this fixture cleanup makes orphan pauses finite.
            for group in set([pid, *groups]):
                assert group > 1
                self.exec("python3", "-c", "import os,signal,sys; os.killpg(int(sys.argv[1]),signal.SIGKILL)", str(group), check=False)
        else:
            self.wait_path("/run/helix-nginx/controller.exit")
            assert self.exec("cat", "/run/helix-nginx/controller.exit").stdout == "1"
            state = yaml.safe_load(self.exec("cat", "/run/helix-nginx/controller.stderr").stdout)
            assert state["status"] != "finished" or state["root"].get("status") == "failed", state

    def apply(self, inputs, *, expected="changed", review=None):
        review = self.controller("check", inputs) if review is None else review
        assert review["status"] in {"changed", "unchanged"}, review
        report = self.controller("apply", inputs, review)
        assert report["status"] == expected, report
        self.preserved()
        return report

    def converged(self, inputs):
        state = self.probe(inputs["port"])
        assert state["package"] == "installed " + inputs["package_version"], state
        assert state["http"] and state["http"]["status"] == 200 and state["http"]["body"] == inputs["content"], state
        assert state["http"]["activation"] == f"1:{inputs['server_name']}:{inputs['port']}", state
        for name, expected in expected_files(inputs).items():
            value = state["files"][name]
            assert value and value["uid"] == value["gid"] == 0 and value["mode"] == 0o644 and value["content"] == expected, (name, value, expected)
        assert state["files"][FILES[2]]["content"] == inputs["content"], state
        self.preserved()
        return state

    def preserved(self):
        state = self.probe()
        assert state["files"]["/var/lib/helix-unmanaged/sentinel"] == self.sentinel, state
        assert all(state["processes"].get(pid) == command for pid, command in self.unmanaged.items()), state

    def cleanup(self):
        # Cleanup has its own bounded budget even after the suite deadline expires.
        self.cleaning = True
        # Query ownership from daemon before removing anything, even for a retained run.
        ids = self.command("ps", "-aq", "--filter", f"label={LABEL}={self.run_id}")
        for identity in ids.stdout.splitlines():
            metadata = self.command("inspect", identity, check=False)
            if metadata.returncode == 0 and json.loads(metadata.stdout)[0]["Config"]["Labels"].get(LABEL) == self.run_id:
                self.command("rm", "-f", identity)
        networks = self.command("network", "ls", "-q", "--filter", f"label={LABEL}={self.run_id}")
        for identity in networks.stdout.splitlines():
            metadata = self.command("network", "inspect", identity, check=False)
            if metadata.returncode == 0 and json.loads(metadata.stdout)[0]["Labels"].get(LABEL) == self.run_id:
                self.command("network", "rm", identity)
        images = self.command("image", "ls", "-q", "--filter", f"label={LABEL}={self.run_id}")
        for identity in set(images.stdout.splitlines()):
            metadata = self.command("image", "inspect", identity, check=False)
            if metadata.returncode == 0 and json.loads(metadata.stdout)[0]["Config"]["Labels"].get(LABEL) == self.run_id:
                self.command("image", "rm", identity)


def desired(**changes):
    return {"package_version": PACKAGE_VERSION, "port": 8080, "server_name": "helix.test", "content": "hello from Helix\n", "state": "present", **changes}


def expected_files(inputs):
    """Independent workload oracle: declared nginx policy, not provider reports."""
    name, port = inputs["server_name"], inputs["port"]
    return {
        FILES[0]: f"# {name}\nworker_processes 1;\npid /run/helix-nginx/nginx.pid;\nerror_log stderr;\nevents {{ worker_connections 64; }}\nhttp {{ include /etc/helix-nginx/server.conf; }}\n",
        FILES[1]: f'server {{ listen {port}; server_name {name}; add_header X-Helix-Activation "1:{name}:{port}" always; root /var/lib/helix-nginx; location / {{ try_files /index.html =404; }} }}\n',
        FILES[2]: inputs["content"],
    }


def baseline(d):
    inputs = desired()
    before = d.probe()
    assert before["package"] is None and before["http"] is None and all(before["files"][path] is None for path in FILES), before
    actions = d.actions()
    resolved = d.controller("resolve", inputs)
    assert resolved == inputs, resolved
    observation = d.controller("observe", inputs)
    assert {"target", "package_version", "files", "service", "response"} <= observation.keys(), observation
    pure = d.controller("plan", inputs, observation)
    first, second = d.controller("check", inputs), d.controller("check", inputs)
    assert pure == first, (pure, first)
    assert first == second and first["status"] == "changed", (first, second)
    assert d.probe() == before and d.actions() == actions
    d.apply(inputs, review=first)
    d.converged(inputs)
    for _ in range(2):
        before, actions = d.probe(), d.actions()
        d.apply(inputs, expected="unchanged")
        assert d.controller("verify", inputs)["status"] == "unchanged"
        assert d.probe() == before and d.actions() == actions, "no-op mutated actual state or emitted an action"


def updates(d):
    inputs = desired()
    d.apply(inputs)
    old = d.probe()
    actions = len(d.actions())
    updated = {**inputs, "server_name": "updated.helix.test"}
    d.apply(updated)
    d.converged(updated)
    performed = d.actions()[actions:]
    assert [(row["operation"], row.get("path")) for row in performed] == [("files-install", FILES[0]), ("files-install", FILES[1]), ("service-reload", None)], performed
    assert {pid for pid, cmd in old["processes"].items() if cmd.startswith("nginx:")} == {pid for pid, cmd in d.probe()["processes"].items() if cmd.startswith("nginx:")}
    actions = len(d.actions())
    changed = {**updated, "content": "second content\n"}
    d.apply(changed)
    d.converged(changed)
    assert [(row["operation"], row.get("path")) for row in d.actions()[actions:]] == [("files-install", FILES[2])], "content-only update mutated unrelated resources"


def drift(d):
    inputs = desired()
    d.apply(inputs)
    changes = [
        ("bytes", ["python3", "-c", "from pathlib import Path; p=Path('/etc/helix-nginx/server.conf'); p.write_text(p.read_text()+'\\n# drift\\n')"]),
        ("mode", ["chmod", "600", FILES[2]]),
        ("owner", ["chown", "65534:65534", FILES[2]]),
        ("missing", ["rm", FILES[2]]),
        ("stopped", [HOST, "service-stop", "{}"]),
        ("package", [HOST, "package-remove", "{}"]),
    ]
    for name, command in changes:
        d.exec(*command)
        drifted = d.probe()
        actions = len(d.actions())
        d.apply(inputs)
        performed = d.actions()[actions:]
        operations = [row["operation"] for row in performed]
        if name == "bytes":
            assert operations == ["files-install", "service-reload"] and performed[0]["path"] == FILES[1], performed
        elif name in {"mode", "owner", "missing"}:
            assert operations == ["files-install"] and performed[0]["path"] == FILES[2], performed
        elif name == "stopped":
            assert operations == ["service-start"], performed
        else:
            expected = ["package-install"] if drifted["http"] is not None else ["package-install", "service-start"]
            assert operations == expected, performed
        d.converged(inputs)
        before = d.actions()
        d.apply(inputs, expected="unchanged")
        assert d.actions() == before, name


def combined_drift(d):
    inputs = desired()
    d.apply(inputs)
    d.host("service-stop")
    d.host("package-remove")
    d.exec("rm", "-f", *FILES)
    d.apply(inputs)
    d.converged(inputs)


def invalid_config(d):
    inputs = desired()
    d.apply(inputs)
    before = d.probe()
    broken = {**inputs, "server_name": "candidate.helix.test"}
    failed_operation(d, "files-install", "invalid-candidate", broken)
    assert d.probe() == before, "invalid candidate changed working state"


def invalid_input(d):
    before, actions = d.probe(), d.actions()
    for change in ({"port": "8080"}, {"port": -1}, {"state": "unknown"}, {"unexpected": True}, {"content": False}, {"server_name": "bad; directive true;"}):
        result = d.controller("check", {**desired(), **change})
        assert result["status"] == "failed", result
    assert d.probe() == before and d.actions() == actions


def stale_review(d):
    inputs = desired()
    d.apply(inputs)
    changed = {**inputs, "content": "reviewed change\n"}
    reviewed = d.controller("check", changed)
    d.exec("chmod", "600", FILES[2])
    before, actions = d.probe(), d.actions()
    result = d.controller("apply", changed, reviewed)
    assert result["status"] == "failed" and d.probe() == before and d.actions() == actions, result
    d.apply(changed)
    review = d.controller("check", changed)
    before, actions = d.probe(), d.actions()
    result = d.controller("apply", {**changed, "content": "unreviewed\n"}, review)
    assert result["status"] == "failed" and d.probe() == before and d.actions() == actions, result
    result = d.controller("apply", changed, {**review, "revision": "older-provider-revision"})
    assert result["status"] == "failed" and d.probe() == before and d.actions() == actions, result
    d.fresh()
    result = d.controller("apply", changed, review)
    assert result["status"] == "failed" and d.probe()["package"] is None, result


def authority(d):
    before, actions = d.probe(), d.actions()
    assert d.controller("check", desired(), grant=False)["status"] == "failed"
    d.fault({"observe": "fail"})
    assert d.controller("check", desired())["status"] == "failed"
    d.fault()
    assert d.probe() == before and d.actions() == actions


def failed_operation(d, operation, mode, inputs=None, *, timeout_ms=120000):
    inputs = inputs or desired()
    review = d.controller("check", inputs)
    assert review["status"] == "changed", review
    d.fault({operation: mode})
    report = d.controller("apply", inputs, review, timeout_ms=timeout_ms)
    d.fault()
    assert report["status"] == "failed", report
    d.preserved()
    return report


def package_failure(d):
    bad = {**desired(), "package_version": "0.0.0-does-not-exist"}
    failed = d.controller("apply", bad, d.controller("check", bad))
    assert failed["status"] == "failed" and d.probe()["http"] is None, failed
    for mode in ("repository", "delay"):
        failed_operation(d, "package-install", mode, timeout_ms=1500 if mode == "delay" else 120000)
        assert all(d.probe()["files"][name] is None for name in FILES)
    locker = "import fcntl,pathlib,time; f=open('/var/lib/dpkg/lock-frontend','w'); fcntl.lockf(f,fcntl.LOCK_EX); pathlib.Path('/run/helix-nginx/lock-held').touch(); time.sleep(60)"
    pid = d.fixture_process(locker)
    d.wait_path("/run/helix-nginx/lock-held")
    report = d.controller("apply", desired(), d.controller("check", desired()))
    d.exec("kill", str(pid))
    assert report["status"] == "failed" and d.probe()["package"] is None, report
    d.apply(desired())
    d.converged(desired())


def file_failure(d):
    d.apply(desired())
    changed = {**desired(), "content": "changed\n"}
    for mode in ("permission-denied", "fail"):
        before = d.probe()
        failed_operation(d, "files-install", mode, changed)
        assert d.probe() == before
    configuration = {**desired(), "server_name": "partial.helix.test"}
    before = d.probe()
    actions = len(d.actions())
    failed_operation(d, "files-install", "replace-failure", configuration)
    partial = d.probe()
    assert partial["files"][FILES[0]]["content"] != before["files"][FILES[0]]["content"]
    assert partial["files"][FILES[1]] == before["files"][FILES[1]] and partial["http"] == before["http"]
    assert not any(row["operation"] == "service-reload" for row in d.actions()[actions:])
    d.apply(configuration)
    d.converged(configuration)


def service_failure(d):
    failed_operation(d, "service-start", "fail")
    assert d.probe()["http"] is None
    occupant = "import socket,pathlib,time; s=socket.socket(); s.bind(('0.0.0.0',8080)); s.listen(); pathlib.Path('/run/helix-nginx/port-held').touch(); time.sleep(60)"
    pid = d.fixture_process(occupant)
    d.wait_path("/run/helix-nginx/port-held")
    report = d.controller("apply", desired(), d.controller("check", desired()))
    d.exec("kill", str(pid))
    assert report["status"] == "failed", report
    d.apply(desired())
    changed = {**desired(), "server_name": "reload.helix.test"}
    failed_operation(d, "service-reload", "fail", changed)
    d.apply(changed)
    d.converged(changed)


def false_success(d):
    failed_operation(d, "package-install", "false-success")
    assert d.probe()["package"] is None and all(d.probe()["files"][path] is None for path in FILES)
    d.apply(desired())
    changed = {**desired(), "content": "not yet installed\n"}
    failed_operation(d, "files-install", "false-success", changed)
    assert d.probe()["http"]["body"] == desired()["content"]
    d.apply(changed)
    d.converged(changed)


def cancellation(d):
    failed_operation(d, "package-install", "delay", timeout_ms=1000)
    assert d.probe()["package"] is None and d.probe()["http"] is None and all(d.probe()["files"][path] is None for path in FILES)
    review = d.controller("check", desired())
    d.fault({"package-install": "delay"})
    pid = d.background("apply", desired(), review)
    d.wait_fault()
    d.kill_controller(pid, signal.SIGINT)
    d.fault()
    assert d.probe()["package"] is None and d.probe()["http"] is None and all(d.probe()["files"][path] is None for path in FILES)
    review = d.controller("check", desired())
    d.fault({"files-install": "delay"})
    pid = d.background("apply", desired(), review)
    d.wait_fault()
    assert d.probe()["package"] == "installed " + PACKAGE_VERSION
    d.kill_controller(pid, signal.SIGINT)
    d.fault()
    assert all(d.probe()["files"][path] is None for path in FILES) and d.probe()["http"] is None
    actions = len(d.actions())
    d.apply(desired())
    d.converged(desired())
    assert not any(row["operation"] == "package-install" for row in d.actions()[actions:])


def controller_death(d):
    # A vanished command peer cannot take down the independently owned service supervisor.
    d.exec("python3", "-c", "import socket; s=socket.socket(socket.AF_UNIX); s.connect('/run/helix-nginx/supervisor.sock'); s.sendall(b'{\"operation\":\"status\"}\\n'); s.shutdown(socket.SHUT_RDWR); s.close()")
    assert d.controller("observe", desired())["service"]["running"] is False
    inputs = desired()
    review = d.controller("check", inputs)
    d.fault({"package-install": "after-mutation"})
    pid = d.background("apply", inputs, review)
    d.wait_fault()
    assert d.probe()["package"] == "installed " + PACKAGE_VERSION
    d.kill_controller(pid, signal.SIGKILL)
    d.fault()
    actions = len(d.actions())
    d.apply(inputs)
    d.converged(inputs)
    assert not any(row["operation"] == "package-install" for row in d.actions()[actions:]), "fresh reconciliation repeated completed package mutation"
    changed = {**inputs, "server_name": "recovered.helix.test"}
    review = d.controller("check", changed)
    d.fault({"files-install": "after-mutation"})
    pid = d.background("apply", changed, review)
    d.wait_fault()
    d.kill_controller(pid, signal.SIGKILL)
    d.fault()
    actions = len(d.actions())
    d.apply(changed)
    d.converged(changed)
    assert [row["operation"] for row in d.actions()[actions:]] == ["service-reload"], "fresh reconciliation failed to distinguish installed configuration from active configuration"


def removal(d):
    d.apply(desired())
    absent = {**desired(), "state": "absent"}
    d.apply(absent)
    state = d.probe()
    assert state["package"] is None and state["http"] is None and all(state["files"][p] is None for p in FILES), state
    before = d.actions()
    d.apply(absent, expected="unchanged")
    assert d.actions() == before


def lifecycle(d):
    d.apply(desired())
    d.command("restart", d.container)
    d.wait_supervisor()
    # The unmanaged process is intentionally not boot-persistent in this fixture.
    d.command("exec", "-d", d.container, "sleep", "86400")
    d.unmanaged = {pid: command for pid, command in d.probe()["processes"].items() if command.startswith("sleep 86400")}
    d.apply(desired())
    d.converged(desired())
    d.fresh()
    baseline(d)


def sensitivity(d):
    d.apply(desired())
    changed = {**desired(), "server_name": "suppressed.helix.test"}
    failed_operation(d, "service-reload", "false-success", changed)
    # A report claiming unchanged cannot replace the actual content oracle.
    d.exec("python3", "-c", "from pathlib import Path; Path('/var/lib/helix-nginx/index.html').write_text('wrong')")
    try:
        d.converged(desired())
    except AssertionError:
        pass
    else:
        raise AssertionError("independent HTTP oracle accepted false no-op")
    d.apply(desired())
    d.converged(desired())


def verify(h):
    names, repeats = options()
    d = Docker(h.root)
    failed = True
    results = []
    previous_termination = signal.getsignal(signal.SIGTERM)
    def interrupted(signum, frame):
        raise KeyboardInterrupt("Docker nginx suite cancelled")
    signal.signal(signal.SIGTERM, interrupted)
    print(f"docker_nginx artifacts: {d.artifacts.relative_to(h.root)}")
    try:
        d.provision()
        for repetition in range(repeats):
            for name in names:
                d.fresh()
                start = time.monotonic()
                d.scenario_deadline = start + 900
                try:
                    globals()[name](d)
                except BaseException as error:
                    results.append(dict(scenario=name, repetition=repetition + 1, status="failed", error=str(error), replay=replay(name)))
                    raise AssertionError(f"{name}: {error}\nreplay: {replay(name)}\nartifacts: {d.artifacts}") from error
                stats = json.loads(d.command("stats", "--no-stream", "--format", "{{json .}}", d.container, timeout=15).stdout)
                results.append(dict(scenario=name, repetition=repetition + 1, status="passed", elapsed_ms=round((time.monotonic()-start)*1000), resources=stats, replay=replay(name)))
                d.scenario_deadline = None
                print(f"docker_nginx {name} repetition={repetition+1}: passed")
        failed = False
    finally:
        signal.signal(signal.SIGTERM, previous_termination)
        active_failure = sys.exc_info()[0] is not None
        cleanup_errors = []
        retained = failed and os.environ.get("HELIX_DOCKER_NGINX_KEEP_FAILED") == "1"
        # Evidence failures cannot prevent cleanup or overwrite the original failure.
        d.cleaning = True
        if d.container:
            for args in (("logs", d.container), ("cp", f"{d.container}:{TRACE}", str(d.artifacts / "actions.jsonl"))):
                try:
                    d.command(*args, timeout=15, check=False)
                except Exception as error:
                    cleanup_errors.append(str(error))
        if not retained and d.daemon_ready:
            try:
                d.cleanup()
            except Exception as error:
                cleanup_errors.append(str(error))
        report = dict(version=1, run_id=d.run_id, status="failed" if failed or cleanup_errors else "passed", retained=retained,
                      elapsed_ms=round((time.monotonic()-d.started)*1000), versions=VERSIONS,
                      scenarios=results, commands=d.events, cleanup_errors=cleanup_errors,
                      cleanup=f"./manage.py docker-nginx cleanup {d.run_id}")
        (d.artifacts / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        if cleanup_errors and not active_failure:
            raise AssertionError("Docker evidence/cleanup failed: " + "; ".join(cleanup_errors))


def dispatch(root, action, run_id, output_format):
    from tooling.output import emit
    if action == "describe":
        if run_id is not None:
            raise ValueError("describe does not take a run ID")
        report = dict(version=1, command="docker-nginx", scenarios=SCENARIOS,
                      run="./manage.py test --suite docker_nginx",
                      environment={"HELIX_DOCKER_NGINX_SCENARIO": "all (default) or one listed scenario",
                                   "HELIX_DOCKER_NGINX_REPEAT": "2 (default), 1..10",
                                   "HELIX_DOCKER_NGINX_KEEP_FAILED": "1 retains failed resources for inspection"},
                      cleanup="./manage.py docker-nginx cleanup RUN_ID")
        if output_format == "json":
            emit(report)
        else:
            for name, meaning in SCENARIOS.items():
                print(f"{name}: {meaning}\n  {replay(name)}")
            print("Default: every scenario, twice. HELIX_DOCKER_NGINX_REPEAT=1 selects a focused first pass.\n"
                  "HELIX_DOCKER_NGINX_KEEP_FAILED=1 retains failed containers.\n"
                  "Cleanup only a named run: ./manage.py docker-nginx cleanup RUN_ID")
        return 0
    if run_id is None:
        raise ValueError("cleanup requires a run ID from runtime/docker-nginx/RUN_ID/report.json")
    d = Docker(root, run_id)
    d.cleanup()
    if output_format == "json":
        emit(dict(version=1, command="docker-nginx", status="cleaned", run_id=run_id))
    else:
        print(f"docker_nginx: cleaned resources labeled {LABEL}={run_id}; artifacts retained")
    return 0


def verify_harness(h):
    """Exercise safety/observation boundaries without requiring Docker infrastructure."""
    from unittest.mock import patch
    import contextlib
    import io
    fixture = h.root / "tests/fixtures/docker_nginx"
    for name in ("host.py", "common.py", "supervisor.py"):
        compile((fixture / name).read_text(), str(fixture / name), "exec")
    compile(PROBE, "docker_nginx independent probe", "exec")
    dockerfile = (fixture / "Dockerfile").read_text()
    validate_versions()
    for name in ("HELIX_BUILDER_IMAGE", "HELIX_RUNTIME_IMAGE", "HELIX_PACKAGE_SNAPSHOT"):
        assert f"ARG {name}\n" in dockerfile and ("${" + name + "}" in dockerfile or f'"${name}"' in dockerfile)
    assert "FROM ${HELIX_BUILDER_IMAGE} AS build" in dockerfile and "FROM ${HELIX_RUNTIME_IMAGE}" in dockerfile
    assert all(callable(globals().get(name)) for name in SCENARIOS)
    with patch.dict(os.environ, {"HELIX_DOCKER_NGINX_SCENARIO": "baseline", "HELIX_DOCKER_NGINX_REPEAT": "1"}):
        assert options() == (["baseline"], 1)
    for name, value in (("HELIX_DOCKER_NGINX_SCENARIO", "unknown"), ("HELIX_DOCKER_NGINX_REPEAT", "0")):
        with patch.dict(os.environ, {name: value}):
            try:
                options()
            except ValueError:
                pass
            else:
                raise AssertionError("invalid Docker suite selector accepted")
    try:
        Docker(h.root, "../../not-owned")
    except ValueError:
        pass
    else:
        raise AssertionError("cleanup accepted invalid ownership identifier")
    d = Docker(h.root)
    try:
        with patch("subprocess.Popen", side_effect=FileNotFoundError):
            try:
                d.command("version")
            except RuntimeError as error:
                assert "prerequisite" in str(error) and "cannot be skipped" in str(error)
            else:
                raise AssertionError("explicit Docker acceptance hid missing prerequisite")
        calls = []
        def fake(*args, **kwargs):
            calls.append(args)
            output = ""
            if args[:2] == ("ps", "-aq"):
                output = "owned\nunowned\n"
            elif args[0] == "inspect":
                output = json.dumps([{"Config": {"Labels": {LABEL: d.run_id if args[1] == "owned" else "someone-else"}}}])
            return subprocess.CompletedProcess(args, 0, output, "")
        d.deadline = 0
        with patch.object(d, "command", side_effect=fake):
            d.cleanup()
        assert d.cleaning, "suite deadline must not disable cleanup"
        assert ("rm", "-f", "owned") in calls and ("rm", "-f", "unowned") not in calls, calls
        # Independent oracle must reject HTTP/content/metadata corruption even if
        # a provider report were to claim success.
        good = {"package": "installed " + PACKAGE_VERSION,
                "http": {"status": 200, "body": desired()["content"], "activation": "1:helix.test:8080"},
                "files": {path: {"content": body, "uid": 0, "gid": 0, "mode": 0o644} for path, body in expected_files(desired()).items()}}
        with patch.object(d, "probe", return_value=good), patch.object(d, "preserved"):
            d.converged(desired())
        for damaged in ({**good, "package": None}, {**good, "http": {"status": 200, "body": "incorrect"}},
                        {**good, "files": {**good["files"], FILES[0]: {"uid": 0, "gid": 0, "mode": 0o600}}}):
            with patch.object(d, "probe", return_value=damaged), patch.object(d, "preserved"):
                try:
                    d.converged(desired())
                except AssertionError:
                    pass
                else:
                    raise AssertionError("independent observation accepted corrupt actual state")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            assert dispatch(h.root, "describe", None, "json") == 0
        assert set(json.loads(output.getvalue())["scenarios"]) == set(SCENARIOS)
    finally:
        shutil.rmtree(d.artifacts)
