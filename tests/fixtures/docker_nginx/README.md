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

Modes are `resolve`, `observe`, `plan`, `check`, `apply` and `verify`. Pure `plan`
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
