# Docker nginx mechanism fixture

The fixture supports one `linux/amd64` Debian bookworm container, nginx
`1.22.1-9+deb12u3`, and one controller at a time. `versions.json` records the
image and builder manifests and Debian snapshot. Cargo uses the repository lock
file. The harness supplies these pins as required Docker build arguments; the
Dockerfile has no independent version defaults. The image installs Python and retains snapshot package indices, but leaves
nginx absent; package application never refreshes those indices. This is an
acceptance environment, not a recommendation to deploy these frozen versions.

Run and discover it through `./manage.py docker-nginx --help` and
`./manage.py test --suite docker_nginx`. The image builds from a minimal repository
context prepared by the suite. The controller runs inside the container and uses
the ordinary authorized command effect. Docker transport belongs to Python test
tooling; Helix source receives no Docker socket or Docker authority.

The user configuration is `tests/programs/docker_nginx.yaml`, with a second input
variant beside the `docker_nginx/operate.yaml` entrypoint. Inside the fixture,
that entrypoint is `/opt/helix-programs/docker_nginx/operate.yaml`. Run it with
`helix perform --allow-stdin --allow-stdout --command-timeout-ms 120000 --allow-command /usr/local/bin/helix-nginx-host`
followed by the entrypoint path. Its single JSON stdin request uses this shape:

```json
{"mode":"check","config":{"package_version":"1.22.1-9+deb12u3","port":8080,"server_name":"helix.test","content":"hello from Helix\n","state":"present"}}
```

Modes are `resolve`, `explain`, `observe`, `plan`, `check`, `apply` and `verify`.
`port` and `state` may be omitted: the shared nginx configuration class supplies
8080 and `present`. Other required fields remain explicit. All operations resolve
and validate purely before observing or changing the target. `explain` returns
`format: helix.nginx.configuration.v1`, resolved `values`, and per-field class
`provenance`, distinguishing explicit values from defaults. Helix callers may
also inherit from `nginx.configuration`, add constraints, and pass that class to
the same library operations; the JSON entrypoint is for ordinary input data. Pure `plan`
also requires a `before` observation; `check` observes then plans. Save the
returned plan and supply that complete value as `review` alongside the same
configuration for `apply`; application re-observes and rejects stale reviews.
Never generate a fresh review implicitly when an operator requested application
of an older one. JSON result data goes to stdout and full execution state to
stderr. Exit 2 means preview drift, 1 means failure, and 0 means successful
resolution/observation, a no-op, or completed application. The harness drives
these same library responsibilities through the normal effect engine.

`host.py` accepts `OP JSON` and emits one JSON value, or exits nonzero with an
`error` value. Its fixed paths are `/etc/helix-nginx/nginx.conf`,
`/etc/helix-nginx/server.conf`, and `/var/lib/helix-nginx/index.html`. Observations
include exact content, numeric UID/GID/mode, package version, persistent fixture
target identity, and actual HTTP response/activation. Missing resources are null;
observation errors are failures. There is no desired-state planner in this code.
The reusable Helix module decides whether to install, replace, activate or remove.

The PID 1 supervisor owns nginx directly and reaps orphaned children. A short
command effect sends it an explicit start/reload/stop request over a root-only
Unix socket; it does not daemonize a subprocess that escapes the effect host's
process-group cleanup. The supervisor never automatically restarts nginx. A
container restart therefore requires observation and reconciliation. This is not
systemd enablement or boot persistence. The supervisor also owns an unrelated
sentinel process; the harness adds its own independently observed sentinel.

Configuration installation stages the complete proposed nginx configuration and
runs `nginx -t` before replacing live paths. Replacement is atomic per file with
ownership, permissions and fsync; several files are not one transaction. Partial
failure is observable and a fresh plan repairs it. Start/reload waits for the
expected response and `X-Helix-Activation` header. Its token combines the provider
revision with the server identity and port, covering the fixed template contract
and its variable inputs. Activation comes from the running server, not from an
acknowledgement journal, so a controller death during a reviewed update cannot
make inactive bytes look activated. This is operational evidence, not attestation
against an outside actor forging the header. A concurrent external mutation
between review and application remains a race in this scope.

File-install errors retain the original kind/message and expose
`completed_paths`: replacements already visible in the filesystem. `uncertain`
and, when present, `uncertain_path` identify a replacement or durability step
whose completion was not confirmed. These details remain in the operator's
structured host error even when no whole resource completed. An orchestration
`changed: false` counts completed resources; it does not negate partial host
mutations or the need for fresh observation after failure.

A reload sends one HUP and keeps the master process. Before acknowledging it,
the supervisor waits up to five seconds for the old worker identities (PID and
start-time tick) to leave the master's child set; the command then checks the
exact HTTP response. One response from a new worker alone is insufficient while
old workers can still serve the previous configuration. A dead master, shutdown
or retirement timeout is a failure; the fixture does not force-kill old workers
and call that a successful reload.

The action trace under `/var/lib/helix-nginx-host/actions.jsonl` records completed
fixture operations for independent assertions. It is not an execution snapshot,
recovery journal, or authority source. Actual package, file, process and HTTP
observations remain necessary even when trace events are present.

Fault injection is fixture-owned: `/run/helix-nginx/fault.json` maps an operation
to `fail`, `false-success`, `delay` or `after-mutation`. File installation also
supports `permission-denied` and `replace-failure`; package installation supports
`repository`; reload supports `suppress-reload`. Pauses expose
`fault-reached.json` and end when `fault-release` appears, the caller terminates
the command, or the 120-second fixture ceiling expires. These hooks are not
accepted as Helix provider inputs. Real package locks, occupied ports and file
metadata drift are injected independently by the harness.

`files-install: invalid-candidate` corrupts only the staged configuration before
the real `nginx -t` check. This proves that candidate validation preserves a live
working configuration without exposing arbitrary nginx directive injection in
the user-facing server-name input.

## Qualification evidence

[`qualification.yaml`](qualification.yaml) records the complete scenario results,
image/package/controller identities, duration, cleanup result and regression
checks for this milestone. Its raw-report path points into the retained
project-local `runtime/docker-nginx/` artifacts, with a SHA-256 digest; those raw
commands, source build context and observations are local evidence, not checked
into Git. Resource figures are samples at scenario completion, not peak memory
measurements. The fixture enforces a 512 MiB memory limit and a 256-process limit.

The qualified host used Ubuntu 26.10, kernel `7.3.0-10-generic`, and a private
Docker 29.9.3 daemon. Builtin seccomp remained active. This host stacks the
AppArmor label as `docker-default//&unconfined`; the stock self-peer signal rule
prevented child reaping and signaling. The private daemon's `--apparmor-profile`
template changed only the signal/ptrace self-peer patterns to
`peer="docker-default{,//&unconfined}"`, preserving the other default rules.
Containers remained unprivileged, without host mounts or host PID access.
This qualification therefore covers that explicit compatibility configuration,
not an unmodified AppArmor template on this host.

The template came from the [Moby AppArmor template](https://github.com/moby/profiles/blob/main/apparmor/template.go);
the qualification record pins the downloaded source and modified template
digests. Both files and their provenance remain under `runtime/docker-engine/`.
Docker documents [daemon AppArmor template customization](https://docs.docker.com/engine/security/apparmor/).
The suite does not install a daemon, alter host security policy or silently
select an unconfined profile; its explicit invocation requires a working Docker
environment with the process operations this workload needs.


## SSH operation

The same `std:nginx` operations accept optional `ssh_target`, a caller-granted
alias. Without it, the local command contract is unchanged. With it, host requests
use `ssh_command` and the provider descriptor requires that capability; plans
retain the alias and observed target identity, so transport/target changes reject
an old review. The workload still requires the fixture's Python helper,
package indices and PID 1 supervisor. It does not bootstrap arbitrary hosts.

From the controller checkout, run the existing entrypoint with a single JSON
stdin request as above, adding `"ssh_target":"fixture"` to `config`:

```sh
./manage.py runtime perform --allow-stdin --allow-stdout --command-timeout-ms 120000 \
  --allow-ssh fixture,host=HOST,user=root,port=PORT,known_hosts=KNOWN_HOSTS,identity=KEY \
  tests/programs/docker_nginx/operate.yaml
```

The caller supplies a pinned host key and identity; neither the alias nor saved
plans grant authority. The disposable acceptance suite creates fresh client/host
keys and publishes SSH only on loopback. It disables password authentication,
forwarding and interactive login prompts. Root access is limited by the test's
container boundary, not by the SSH grant itself.

For durable application add `--checkpoint NEW_FILE`. Following controller death,
`--resume-checkpoint FILE` refuses an unacknowledged request. Observe the target
independently, record the exact request's actual outcome through `runtime
reconcile`, then resume with fresh grants as described in
[durable effect checkpoints](../../../HOST.md#durable-effect-checkpoints).
The journal does not prove an operator's observation or prevent a separate
controller from changing the target. Files, output and ordinary configuration
values may be present in clear text; private journal permissions are not redaction.

Run `./manage.py test --suite ssh_nginx` for the local/SSH comparison and
controller-death recovery sequence. It uses the same pinned platform and resource
semantics as local acceptance; no systemd, rollback or fleet claim follows.

[`ssh-qualification.yaml`](ssh-qualification.yaml) records the separate SSH
acceptance runs, exact controller/source identities and retained raw-report
digests. Its claim is bounded by the supported environment above.
