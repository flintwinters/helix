# Helix Host Operations Reference

Inventory, resource lifecycles and caller-authorized host effects. These APIs use
the existing [language and evaluation contracts](LANGUAGE.md); reusable data and
testing helpers are cataloged in the [standard library reference](LIBRARY.md).

## Host inventory

`std:inventory.resolve(document, dynamic, selection)` resolves ordinary data into
a reviewable host target list. It is pure and can run inside `type_time`.
Dynamic discovery belongs to the caller: pass its results as `dynamic`, never a
discovery callback. Neither inventory data nor a successful resolution grants
permission to connect, escalate privileges or mutate a host.

The version 1 document contains `version: 1`, a `hosts` list, a `groups` map from
group names to host-ID lists, and an ordered `overrides` list. Each host has a
stable, nonempty `id` and a `connection`. IDs identify inventory entries rather
than network addresses; static and dynamic entries share one identity namespace.
Dynamic input is a list of `{source, hosts}` batches supplied explicitly by the
caller. Their source labels must be nonempty and unique; `static` is reserved
for the document's own hosts. Groups may refer to hosts from either input.

Connection settings describe `transport: local` or `transport: ssh`. SSH requires
an `address` and accepts `port`, `user` and `jump_host`; local connections reject
SSH-only settings. These are connection descriptions, not an SSH implementation
or credential store. Unknown fields are errors, including attempted credential
or authority fields.

Group names must be nonempty. Groups have no settings and impose no configuration
precedence. Each override contains a nonempty `source`, host and group selector lists (`hosts`, `groups`),
and a `connection` patch. Overrides apply in list order; fields replace earlier
values explicitly. Resolution retains provenance so the original connection and
each subsequent override can be inspected. The first provenance entry is
`{source, connection}` with the complete base connection; subsequent entries are
`{source, index, connection}`, where `index` is the zero-based document override
position and `connection` is the applied patch. Override source labels may repeat;
indices identify their declarations. A patch cannot implicitly delete a
field: changing SSH to local while retaining SSH fields is invalid.
Base connections and each intermediate patched connection must be valid; later
overrides cannot repair an invalid earlier definition.

Selection uses `all`, `hosts`, `groups`, `exclude_hosts` and `exclude_groups`.
Host and group inclusions form a union; exclusions win. Selecting nothing never
implicitly selects the entire inventory. Targets follow canonical host-ID order,
independent of input ordering. Missing references and malformed entries remain
errors even when they would not be selected: validation precedes selection, and
an invalid result contains no actionable targets. Selection fields are optional:
`all` defaults to false and selector lists default to empty.

The versioned report is `{version: 1, status, targets, diagnostics}`. A `ready`
report has no diagnostics. Each target contains `id`, `connection`, its sorted
`groups` and ordered `provenance`. An `invalid` report has an empty target list
and the first structured Helix error in `diagnostics`, including its `kind`,
`message`, source location and available error `data`. Schema violations use
the existing type diagnostics; inventory-specific errors distinguish duplicate
hosts, invalid or duplicate dynamic sources, and missing hosts or groups.
Resource-limit failures can still terminate evaluation; callers must require a
finished evaluation and `status: ready` before consuming targets.

Run the [complete example](tests/programs/host_inventory.yaml) with
`./manage.py run tests/programs/host_inventory.yaml`. It combines static and
supplied dynamic hosts, patches a group's SSH port explicitly, and excludes a
host from the resulting review list. `./manage.py test --suite host_inventory`
checks the contract with an independent selection oracle and invalid-input cases.

This module supports bounded in-memory inventories using the existing immutable
collection operations and runtime budgets. It does not contact hosts, establish
reachability, gather facts, cache discovery, or resolve secrets. A host ID is not
proof that two different IDs refer to different physical machines. Connection
adapters must separately validate their supported settings and obtain caller
authority before use.

## Resource composition

`std:orchestration.plan(document)` validates a complete resource graph without
executing resource callbacks. `run(document, execute)` validates the graph again
before invoking `execute(kind, id)` through ordinary Helix function calls. The
callback may use the existing effect engine with caller-supplied grants; the
orchestrator neither creates authority nor introduces another evaluator.

The version 1 document contains `version: 1`, `resources` and `handlers`. Both
collections are maps keyed by nonempty IDs, in separate namespaces. Each resource
has `requires: [resource IDs]` and `notify: [handler IDs]`; each handler has
`requires: [resource IDs]`. All fields are explicit. Unknown fields, missing
references and resource dependency cycles invalidate the complete graph, including
unused handlers and otherwise runnable independent resources. Dependencies reuse
`std:dependency` and its deterministic ordering. Handlers cannot depend on other
handlers, notify handlers, or be prerequisites of resources.

Resources run sequentially in dependency order. A callback receives `kind`
(`resource` or `handler`) and its `id`, and returns a record with `status`
(`changed`, `unchanged`, `failed` or `skipped`) and optional string `details`.
Only `changed` and `unchanged` satisfy prerequisites. A callback error or invalid
result becomes a failed outcome; dependent resources are skipped while independent
resources continue. Successful earlier changes remain visible in the report.
Callbacks must classify their outcomes truthfully: this contract does not infer
idempotence, validate a real resource's postconditions or roll back mutations.

After resources finish, handlers run in canonical handler-ID order. Any changed
resource can notify a handler; duplicate notifications coalesce into one execution.
Unchanged resources do not notify. An activated handler requires successful outcomes
from every resource that declares it as a notification target, plus its explicit
prerequisites. A failed or skipped potential notifier therefore suppresses the
shared handler even if another resource changed successfully. Unnotified handlers
are skipped. There is one deferred handler phase, with no handler chaining or
mid-run flush.

The plan report contains `version: 1`, `status` (`ready` or `invalid`), `order`
(resource IDs), `handlers` (handler IDs), and `diagnostics`. Invalid plans return
empty orders and the first structured error. The run report contains `version: 1`,
`status` (`finished`, `failed` or `invalid`), `changed`, per-ID `resources` and
`handlers` outcome maps, and `diagnostics`. Preflight failure returns no outcomes
and `changed: false`. Otherwise `changed` retains any reported change even when
the run failed. Automatically skipped outcomes distinguish `reason: not_notified`
from `reason: prerequisite_failed`. Unnotified handlers do not fail the run;
other failed or skipped outcomes do. Thrown or malformed callback results retain
the structured `error` alongside `status: failed`. A non-function executor is
an API type error even for an empty graph.

Planning is pure and suitable for `type_time` when supplied ordinary graph data.
The preflight guarantee covers callbacks invoked by `run`: ordinary evaluation of
the caller's argument expressions happens before the function call. Resolve module
inputs and assemble the graph purely before execution. Callback effects suspend
and resume through the same VM continuations as any other Helix function. These
are in-memory execution guarantees, not durable recovery or exactly-once external
mutation guarantees. Caught error data can contain function or machine values;
their emitted inspection graphs do not promise executable restoration. Resource
limits can terminate evaluation; callers must inspect both VM and report status.

The dependency planner and immutable folds target bounded configurations and retain
the normal iteration, work and data limits. This module composes callbacks; resource
providers, real service-manager semantics, fleet scheduling and durable recovery
remain separate contracts.

The [resource composition example](tests/programs/resource_composition.yaml)
imports a versioned, vendored configuration module with typed input and output.
That module imports its own pinned service-policy dependency and returns an
`orchestration.document`; execution remains an ordinary callback supplied by its
caller. Run it with `./manage.py run tests/programs/resource_composition.yaml`.
The example simulates changed outcomes without managing a real service.
`./manage.py test --suite resource_composition` additionally observes real file
effects, checks notification counts and failure gates, and verifies relocation and
missing dependencies. See the [dependency pinning convention](LANGUAGE.md#pinned-dependencies).

## Resource provider contract

`std:provider` supplies a version 1 lifecycle for one explicitly selected resource.
It uses ordinary functions and the existing effect engine. `plan(provider, args,
before, context)` validates supplied observations and returns a pure plan;
`check(provider, args, context)` obtains observations and plans without calling the
application callback. Mutation requires a separate
`apply(provider, args, context, reviewed)` call with the complete reviewed report.

A provider is a record with these required fields:

| Field | Contract |
|-------|----------|
| `version`, `id`, `revision` | Interface version `1`, nonempty provider identity and revision strings |
| `arguments`, `observation`, `result` | Helix types for arguments, observations and application results |
| `platforms`, `capabilities` | Supported platform names and required capability names |
| `check`, `diff` | Booleans declaring preview and diff support |
| `failure` | `stop`: an error stops this resource lifecycle; no automatic retry or rollback |
| `observe` | Function `(args) -> observation`, responsible for reading actual state |
| `plan` | Pure function `(args, before) -> steps` |
| `apply` | Function `(args, steps) -> result`, responsible for performing the changes |

Context contains `platform`, `capabilities` (a list of names), `managed` and
`allow_remove` (booleans). Platform and capability declarations are checked before
observation. An unsupported platform, missing declared capability, or unavailable
check/diff support returns `unsupported` without executing resource callbacks.
`managed: false` returns `skipped`. These context values are caller policy data;
they do not grant effects. Actual authority always comes from the host's explicit
grants. Observation callbacks are trusted provider code and must be read-only;
checking does not make arbitrary callback code harmless.

Steps are ordinary-data records `{field, op, to}` with optional `from`. Each
`field` is a nonempty resource field name and appears once; `op` is `set` or
`remove`. Set steps use the existing `converge` representation; providers may
reuse `drift` and `converge` for class-based desired state. Their list order is
execution order. An empty list means the observed resource satisfies the requested
state. Missing configuration means no requested change,
never implicit deletion. Providers must preserve unmanaged state and return an
explicit `remove` step for deletion; such steps require `allow_remove: true` as
well as management ownership. Providers define the interpretation of values and
must report unknown or inaccessible observations as failures rather than absence.

Validation, including refinement predicates, and planning run inside `type_time`.
Arguments, observations and steps must be ordinary serializable data; functions
and machines cannot be embedded in a reviewed plan. Effects from planning or type
predicates are rejected by the runtime. Inline, embedded and file-imported
providers share this boundary; file modules are authorized and retained by the
loader before evaluation. Reproducible provider source requires pinning transitive
module contents in addition to the author-declared revision.

Successful plans contain `version`, `provider`, `revision`, `args`, `context`,
`before`, `steps`, `status` (`changed` or `unchanged`), `phase: plan` and
`applied: false`. `changed` at this phase means changes are planned, not already
performed. Application observes and plans again, then compares the canonical
YAML encodings of the complete fresh and reviewed reports before mutation.
Numeric kinds and signed zero must match, including inside arguments, observations
and steps; map presentation order does not matter. Ordinary language `eq` is
insufficient because callbacks can distinguish equal numeric representations.
Migration: a representation-only change now produces `provider_stale_plan`;
obtain and review a fresh plan before applying it. Encoding both reports adds
bounded serialization work under the existing runtime budgets. This detects
changed inputs or observations; it does not provide a lock against changes between observation and
mutation. Provider revisions are declarations, not verified content hashes.
Provider authors must include target identity in arguments and change revisions
when callback behavior changes; captured callback environments are not recorded
in plan identity.

After applying, the lifecycle checks the declared result type, obtains fresh
observations and replans. Only an empty fresh plan establishes success. A successful
callback exit alone is insufficient. Verification reports retain `before`, the
complete fresh check report in `after`,
`result`, the applied `steps`, `phase: verify` and `applied: true`. An invalid
application result stops before re-observation. `attempted: true` marks entry
into the application callback; `applied: true` means that callback returned, not
that its postconditions passed. A callback failure can leave external changes
even when `applied` is false. Failures are structured reports with `status: failed` and the originating error; partial external
changes remain possible and are not rolled back. Callers must inspect the report
status as well as VM completion. A normal returned failure report does not itself
make the interpreter process fail.

The outcome vocabulary is `unchanged`, `changed`, `failed`, `skipped` and
`unsupported`. To use a verified provider result with `std:orchestration`, return
its supported status and optional string `details`; explicitly map `unsupported`
to `failed` or `skipped` according to caller policy. Do not pass the full provider
report as an orchestration outcome or notify handlers from a preview's `changed`
status. This lifecycle contract itself supplies no platform resource library, durable recovery,
secret redaction, rollback or exactly-once external execution guarantee.

Run the [pure planning example](tests/programs/provider_contract.yaml) with
`./manage.py run tests/programs/provider_contract.yaml`. The routinized
`./manage.py test --suite provider_contract` suite additionally observes and
changes real files under its project-local artifacts directory, checks state
independently, and exercises rejected previews, stale reviews and failed
postconditions. These are lifecycle conformance tests, not a supported file
resource implementation.

## Guarded command resources

`std:command` adapts arbitrary local commands to the [provider lifecycle](#resource-provider-contract).
It requires an explicit observation function, execution guard, change predicate
and failure predicate. There is no default exit-code policy, inferred idempotence
or reversibility. Use `make(config)` to construct a provider and the module's
`plan(provider, args, before, context)`, `check(provider, args, context)` and
`apply(provider, args, context, reviewed)` functions to operate it.

Configuration supplies `id`, `revision`, `platforms`, `capabilities`, the
`observation` type, and these callbacks:

| Callback | Contract |
| --- | --- |
| `observe(args)` | Read-only trusted callback returning ordinary observation data; unknown or inaccessible state must fail |
| `guard(args, before)` | Pure boolean: `true` requests execution; `false` means the postcondition already holds |
| `failed(args, reply)` | Pure boolean: `true` rejects the command result |
| `changed(args, reply)` | Pure boolean classifying a successful command's actual change |

`capabilities` must include `command` and any capabilities needed by observation.
Command replies contain `code`, `stdout` and `stderr`; successful application
results contain `{reply, changed}`. Predicate rejection returns a structured
`command_failed` error with the reply attached.

Arguments are the existing command payload, `{program: string, args: [string, ...]}`.
The host executes the program directly with that argument vector; shell expansion
requires explicitly selecting a shell. Host executable grants, time/output limits,
cancellation and retry policy remain those of the existing command adapter.
Capability names in configuration and context confer no authority.

Planning evaluates the guard without running the command. Application requires
an exact fresh review match, executes once through the effect boundary, and
re-observes: the guard must then be false. A successful command exit or a false
change predicate cannot bypass this postcondition. Failure takes precedence over
change classification. Predicates execute in `type_time` and must return booleans.

Use `std:command.apply` for the final outcome: after successful verification it
reports `changed` or `unchanged` according to the change predicate. Calling the
generic `std:provider.apply` directly retains its planned-change status instead.
A preview's `changed` means execution is proposed; it does not predict whether
the command will report an actual change. Raw command output is not a content
diff, and this adapter does not redact secrets. The existing provider caveats
about trusted callbacks, declared revisions, source pinning, races between
observation and mutation, partial failures and lack of rollback all apply.

The [pure example](tests/programs/command_provider.yaml) demonstrates the explicit
policy. `./manage.py test --suite command_provider` exercises real subprocesses
and independently inspects project-local files. This supports the bundled local
adapter; it does not supply SSH transport or qualify arbitrary programs as safe,
idempotent resource implementations.

## Host effects and explicit grants

`{$call: [effect, {$ref: operation}, {$ref: payload}]}` yields an explicit request after evaluating its two arguments. Type time rejects effects before yielding. `Vm::run()` stops with `status: signaled`; the state contains `pending_effect` (identity, operation and payload). A host supplies a success value or structured failure through `resolve_effect`. Completed requests and outcomes remain in `effects`; resolving an already completed request does not execute it again. The versioned execution snapshot preserves this state across processes; durable host dispatch additionally requires the journal described below.

Host applications use `Vm::signal(event)` to interrupt an active machine and
`resume_signal()` to continue it. Events are bounded ordinary YAML data, visible
under `signal` while interrupted. A pending request survives interruption;
resolving it does not clear the interrupt. The bundled host leaves interrupted
machines alone. These VM event APIs are separate from the bundled command host's
OS signal cancellation described below. Retained machine branches remain independent: explicitly handing an
older pending branch to a host can perform its request again. Request IDs are
local to a branch, not global deduplication keys.

An effect failure does not promise rollback of partial external changes. The
VM never automatically retries requests. The bundled command host can make bounded
attempts within one request only under the caller's explicit retry policy. Failed
machines with completed effect records do not emit pure recovery source that could
repeat those actions.

`helix perform [grants] file.yaml [-- arguments...]` runs requests through the bundled host. It emits program stdout to stdout and full state to stderr. Source comes from a file so stdin remains available to the program. `run` and `exec` never acquire host capabilities. Use `perform --help` to discover grants. Repeated root, name, executable and origin flags accumulate; grants belong to the caller, never to source or saved state.

| Operation | Payload | Result | Grant |
| --- | --- | --- | --- |
| `args` | `null` | List of arguments after the source filename | None |
| `exit` | Integer 0–255 | `null`; sets successful process exit code after evaluation finishes | None |
| `file_read` | Path string | UTF-8 text | `--allow-read ROOT` |
| `file_write` | `{path: string, text: string}` | `null` | `--allow-write ROOT` |
| `env` | Variable name | String, or `null` if absent | `--allow-env NAME` |
| `stdin` | `null` | UTF-8 text read until EOF | `--allow-stdin` |
| `stdout` | Text string | `null` | `--allow-stdout` |
| `command` | `{program: string, args: list_of_strings}` | `{code, stdout, stderr}` | `--allow-command EXECUTABLE` |
| `ssh_command`, `ssh_file_read`, `ssh_file_write` | See [SSH remote commands](#ssh-remote-commands) | See below | `--allow-ssh ALIAS,host=H,known_hosts=FILE[,user=U][,port=N][,identity=FILE][,jump=J][,become=U]`, optional `--ssh-control-dir DIR` |
| `http` | Absolute HTTP(S) URL string | `{status, body}` | `--allow-http ORIGIN` |
| `sqlite` | `{path: string, sql: string, params?: list, write?: boolean}` | `{columns, rows, changes}` | `--allow-read ROOT`, or `--allow-write ROOT` when `write: true` |
| `time` | `null` | Unix epoch milliseconds | `--allow-time` |
| `random` | `null` | Nonnegative 63-bit integer | `--allow-random` |

File imports under `perform` require the same `--allow-read` roots (embedded standard modules remain available). Relative file-effect paths resolve against the host working directory; imports retain source-relative resolution. File roots are canonicalized; existing symlinks and parents of new files must remain within a granted root. These checks are import-style confinement, not protection against concurrent filesystem changes or an OS sandbox. Commands use argument vectors without an implicit shell, inherit the host environment and working directory, and receive closed stdin. An executable grant authorizes that executable's ambient access, including subprocesses; granting a shell authorizes shell commands. Nonzero command exits are result data; spawn, encoding and host failures are catchable errors. The `exit` effect selects the eventual successful exit code and does not skip cleanup or later evaluation.

`sqlite` runs one statement (bound `params` of null, boolean, number or string) against a database inside the granted roots, with the bundled SQLite. A read request opens the file read-only; `write: true` may create it and reports `changes`. `ATTACH`/`DETACH` are denied so a statement cannot leave the authorized path, multiple statements and blob results are errors (kind `sqlite`, `conversion`), results are limited to 1 MiB and failures are catchable. The database must exist for a read request. Each request opens its own connection, so transactions across requests, rollback and concurrent-writer coordination beyond a ten-second busy timeout are the caller's concern. `std:sqlite` wraps this effect.

HTTP uses the installed `curl` executable for GET requests, disables curl configuration, proxies, URL globbing and redirects, and restricts protocols to HTTP(S). An origin grant includes scheme, authority and explicit port exactly as written. HTTP error statuses remain result data. File text and HTTP responses are limited to 1 MiB. HTTP retains an eight-second request deadline and a ten-second process deadline, independently of command policy. Stdin waits for EOF.

### SSH remote commands

Three operations reach a granted target through the installed OpenSSH client under the same command policy (total timeout, output bounds, progress, cancellation, explicitly classified retries) as local commands:

| Operation | Payload | Result |
| --- | --- | --- |
| `ssh_command` | `{host: alias, program: string, args: list_of_strings, stdin?: string, become?: user}` | `{code, stdout, stderr}` |
| `ssh_file_read` | `{host, path, become?}` (absolute path) | UTF-8 text |
| `ssh_file_write` | `{host, path, text, mode?: "0644", become?}` (absolute path) | `null` |

Source names only an alias. The caller's `--allow-ssh ALIAS,host=H,known_hosts=FILE[,user=U][,port=N][,identity=FILE][,jump=[U@]H[:P]][,become=U]` grant fixes host, port, user, identity file, optional jump host and the allowed `become` users. A grant must supply `known_hosts`: keys are verified strictly against that file only, never trusted on first use, and user or system ssh configuration, agent and X11 forwarding and port forwards are disabled. Authentication is non-interactive (`BatchMode`) with the granted identity or the caller's agent. A `jump` hop runs under the same host-key file, identity and `BatchMode` policy (the file must list the jump host's key too). The grant is a connection and execution authority, not an OS sandbox: it authorizes whatever the remote account may run.

The remote command line quotes every word, so arguments reach the remote program byte-for-byte without shell interpretation, word splitting or expansion; a NUL byte is a type error. `stdin` supplies text (at most 1 MiB) on the remote program's standard input. `ssh_file_write` streams `text` on stdin to a temporary file beside the target, verifies the received byte count, applies the mode and renames it into place, so readers never see a partial file and a failed or truncated transfer leaves the previous file untouched and removes the temporary one; it is per-file atomic, not a multi-file transaction. New files are owned by the account that executed the command, and default mode is `0644`. Reads and writes are UTF-8 text only (non-UTF-8 content is an `io` error); there is no binary or directory transfer. The remote host needs `sh`, `cat`, `mktemp`, `wc`, `chmod`, `mv` and `rm`; `std:ssh` checks and can bootstrap them (see [LIBRARY.md](LIBRARY.md)).

Every request uses a remote `sh` wrapper to report command completion with a per-request nonce. Remote exit codes, including 255, remain result data rather than SSH failures. With `become`, `sudo -n -u USER -- sh -c ...` runs the reporting wrapper and command together; policy must authorize this shell wrapper. There is no separate `sudo true` precheck. A sudo invocation without a completed inner report is `ssh_privilege`; missing remote completion is `ssh_remote`. A failed file operation is `ssh_file`, and an ungranted target or identity is `capability`. The framing distinguishes ordinary output from reports; it is not a security boundary against a remote program that can inspect its parent process arguments or manipulate the reporting shell.

OpenSSH exposes pre-session host-key, authentication and connection failures through the same exit status 255. Their current `ssh_host_key`, `ssh_auth` and `ssh_connection` subdivisions still parse client diagnostics and are heuristic; unrecognized diagnostics retain the raw message as `ssh_connection`. Reliable typed pre-session errors require an SSH protocol API rather than the OpenSSH command's exit status. Timeouts and cancellation retain the local command rules; the remote host may already have changed.

`--ssh-control-dir PRIVATE_DIR` enables connection reuse. The directory is created owner-only if absent and refused if group- or world-accessible. Each `perform` invocation creates a private session subdirectory, so restored or concurrent controllers never inherit earlier authenticated sockets. Requests within that session share connections only for identical grants (`ControlMaster=auto`, keyed by the full grant and OpenSSH target identity, kept alive at most 30 seconds), and `perform` tears the masters down when the session ends. Reuse is a caller opt-in because a socket confers the authenticated session on anyone who can open it. The directory must be on a local filesystem that allows hard-linking sockets: OpenSSH creates them with `link(2)`, which NFS refuses, and the request then fails with `ssh_connection`. If the controller itself is killed, a master may outlive it for up to 30 seconds.

Not provided: password or interactive authentication, binary transfer, directory synchronization, remote environment control and fact gathering. `tests/suites/ssh_host.py` verifies the contract against unprivileged loopback `sshd` servers, including a jump hop and independently counted authentications (`./manage.py test --suite ssh_host`), look-alike remote output and remote exit 255. `HELIX_SSH_DOCKER=1 ./manage.py test --suite ssh_host` additionally provisions a disposable Debian SSH host and verifies root execution, stdin, file ownership and real sudo-policy refusal. It uses `sudo -n docker` and removes its container and image afterwards.

### Bounded command operations

The caller configures command policy on `EffectHost`; source and saved state cannot
increase these limits or authorize retries. `perform` exposes the same policy:

| Flag | Default | Accepted range and meaning |
| --- | --- | --- |
| `--command-timeout-ms` | 10000 | 1–86400000 milliseconds for the whole request, including attempts and retry delays |
| `--command-output-limit` | 1048576 | 1–16777216 bytes per stream, accumulated across attempts |
| `--command-attempts` | 1 | 1–32 total attempts |
| `--command-retry-delay-ms` | 0 | 0–60000 milliseconds between attempts |
| `--command-retry-exit-code` | None | Repeatable integer 1–255 explicitly classified by the caller as transient and safe to retry |
| `--command-progress` | None | New JSONL file for versioned progress metadata; existing paths are refused |

A successful command result keeps `{code, stdout, stderr}` and contains the final
attempt's output. Earlier attempts still consume the shared output budget. Exceeding
the output limit fails instead of silently truncating. VM value and data budgets
still apply when the captured result enters the machine. An unclassified nonzero exit
or exhaustion of the attempt count returns the final exit as ordinary result data.
Timeout, cancellation, output overflow, encoding, spawn and process-lifecycle
failures never trigger retries. An exit-code classification is a caller assertion
about a particular command's semantics, not proof that an operation is idempotent.
The policy applies to every command request handled by that host; classifications
must be safe for every granted executable that uses them.

Progress reports attempt transitions, elapsed time and byte counts, including
heartbeats for quiet commands. They exclude executable names, arguments and output;
program stdout and YAML state on stderr retain their existing contracts. Progress
is observational host data, not a durable recovery journal. Library callbacks and
progress storage must remain responsive; their own blocking IO is outside the
command deadline guarantee.

Each JSONL record has `version: 1`, branch-local `effect_id`, one-based `attempt`,
`event`, `elapsed_ms`, cumulative `stdout_bytes` and `stderr_bytes`, and nullable
`code`. Events are `starting` (before spawn, also checking the progress sink),
`started`, `output`, `heartbeat`, `retry`, `finished`, or the failure categories
`timeout`, `cancelled`, `resource` and `failed`. This metadata does not replace the
final effect outcome in machine state.

On Unix, commands execute in a fresh process group. Timeout and cancellation kill
that group and reap the immediate child; normal completion also cleans up remaining
group members. An exited command whose descendants keep capture pipes open fails
as an uncertain process outcome and is not retried. Commands must run synchronously
and must not daemonize. `perform` handles SIGINT and SIGTERM as cancellation; library
callers set `EffectHost.cancellation`, a shared atomic flag. Cancellation also gates
subsequent effect dispatch: catching a command cancellation cannot authorize another
host mutation. The host interrupts at the next pending effect until the caller
explicitly clears cancellation and resumes the signal. Pure evaluation and blocking
non-process adapters are not made preemptible by this command cancellation policy.
`perform` exits 1 after cancellation even when source catches the error and returns
a pure value, in which case the emitted VM state can still be `finished`.

This is local process lifecycle control, not an OS sandbox or rollback. It cannot
clean up after SIGKILL or controller crashes, contain descendants that deliberately
leave the process group, or establish whether remote mutations survived a broken
SSH connection. Cancellation does not undo completed external changes. Caller-owned journals below retain uncertain requests for explicit reconciliation;
provider-specific observation is still needed to establish what happened remotely.

`--fixed-time MILLISECONDS` fixes the clock response. `--seed INTEGER` selects a reproducible random sequence, advanced once per request; both still require their grants. Without these options the host reads the system clock and `/dev/urandom`. The deterministic generator is intended for tests and replay inputs, not cryptographic randomness. Effect request/outcome histories expose nondeterministic inputs for inspection; versioned snapshots preserve active continuations across processes, and journals retain completed outcomes without re-executing them. Capability failures occur before host actions and can be handled with `try`/`finally` through the ordinary evaluator.


## Durable effect checkpoints

`./manage.py runtime perform --checkpoint run.json [grants] program.yaml`
creates a new, private execution journal. Resume with
`./manage.py runtime perform [fresh grants] --resume-checkpoint run.json`.
An explicit resume acknowledges a saved interruption before continuing.
The host exclusively locks that checkpoint path, flushes an `in_flight` record
before each effect, then atomically replaces and flushes it with the completed
outcome. Completed effects are retained in the machine. Exit status and seeded
random-generator progress are host bookkeeping stored alongside the snapshot;
command, file, SSH and other grants are never stored or restored.

Inspect a journal with `./manage.py runtime checkpoint run.json`. The versioned
`helix.recovery.v1` report includes completed effect outcomes, source locations,
the exact pending request, and the next action. It validates the embedded snapshot
without running code, acquiring the writer lock, or creating files; it can inspect
a live controller's last durable version. `observe_and_reconcile` means resume is
blocked by uncertainty. `resume_with_fresh_grants` means no uncertain outcome is
recorded, not that the target is unchanged or that another controller has stopped.
`complete` and `failed` describe terminal execution. Inspection is a point-in-time
view; reconciliation still validates the exact request under the exclusive lock.

A controller that dies after dispatch but before acknowledging the result leaves
an uncertain request. Resume refuses to repeat it. Observe the target independently,
then supply `./manage.py runtime reconcile run.json observed.yaml`, for example:

```yaml
request:
  id: 2
  operation: command
  payload: {program: /path/to/helper, args: [apply]}
outcome:
  ok: {code: 0, stdout: '', stderr: ''}
```

The entire request (id, operation and payload) must match. Alternatively use
`outcome: {error: {kind: interrupted, message: 'Observed partial application'}}`.
Reconciliation records that caller-supplied observation without performing the
original effect; the next resume continues ordinary success or error handling.
It cannot certify that the observation is true. Seeded random reconciliation
checks the supplied result against the saved deterministic generator state.

These are Unix controller journals, not distributed transactions. A file lock
protects one journal, not a target shared by multiple journals. Copying a journal
creates an independent branch and may repeat future effects. An uncertain remote
operation may still be running; observing it and choosing a safe outcome remain
provider/operator responsibilities. Immutable VM state does not roll back host
mutations. Checkpoint files can contain captured data and command output; private
file permissions are not the charter's future secret-handle/redaction contract.
Disk-full, rename and flush failures stop the controller; an already dispatched
effect remains uncertain when its outcome could not be durably saved.
