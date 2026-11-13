# Helix

Helix is an experimental language and runtime written in Rust. Programs are YAML;
results and errors are YAML too. The idea is to make execution state as easy to
read, diff, and edit as source code—and eventually to resume a program from that
state.

Today machines can run, pause between explicit transitions, and branch immutably in memory. Disk save/restore and the reversibility promise are not implemented.

External operations use explicit effect requests. A host authorizes and resolves
each request; pending requests and completed outcomes remain visible in VM state.
The `perform` command supplies file, process, environment, stream, HTTP, clock and
randomness adapters with caller-selected grants. See `helix perform --help` for
the capability flags. Ordinary `run` yields at an effect without executing it.
Host events can interrupt and resume in-memory machines through the public VM API.
These APIs do not restore continuations from emitted YAML.

Long-running local commands have caller-selected time and output budgets, optional
metadata progress, Unix process-group cancellation, and bounded retries for explicitly
classified exit codes. Discover the policy with `./manage.py runtime perform -- --help`;
see [command operation semantics and limits](LANGUAGE.md#bounded-command-operations).

## Try it

You need Rust with Cargo (2024 edition support) and Python 3.10+.

```sh
./manage.py run tests/cases/arithmetic.yaml
```

This builds the runtime and runs:

```yaml
x: 2
y: {$call: [add, {$ref: x}, 1]}
z: {$call: [mul, {$call: [sub, {$ref: y}, 1]}, 5]}
```

An explicit call `{$call: [add, {$ref: x}, 1]}` adds one to `x`. The result is:

```yaml
status: finished
root:
  x: 2
  "y": 3
  z: 10
```

The emitter quotes strings where YAML could otherwise change their meaning.
Wrap an ordinary-data `root` in `{$literal: ...}` to run it again without interpreting reserved maps as code. Functions and machines use named object inspection graphs; these are not executable snapshots.

For a result without the state envelope:

```sh
./manage.py runtime exec '{$call: [add, 20, 22]}'
# 42
```

After building, use `target/debug/helix` directly, or install it with
`cargo install --path .`. `helix exec` accepts inline source or reads stdin;
string results are written verbatim, other values as YAML, and errors go to stderr.
Run `helix --help` for the other commands.

For [jq](https://jqlang.org/manual/) and [yq](https://mikefarah.gitbook.io/yq/commands/evaluate)
pipelines, select an explicit output format so string results remain quoted data:

```sh
helix exec --json '{answer: {$call: [add, 20, 22]}}' | jq '.answer'
helix exec --yaml '{answer: {$call: [add, 20, 22]}}' | yq '.answer'
```

Both flags work with `./manage.py runtime exec` and stdin. JSON input is accepted
as YAML source with the same explicit call/reference forms. Arrays are data sequences.
Use `set -o pipefail` to detect failures anywhere in a shell pipeline.
The `pipeline` suite checks encodings and actual consumers when installed;
CI requires `jq` and `yq` via `HELIX_REQUIRE_PIPELINE_TOOLS=1`.

## A little more language

Helix has immutable values, lexical scope, functions, recursion, collections,
imports, first-class types, and structured errors with source locations.
Strings are always literal; `{$ref: name}` looks up a name. `{$call: [actor, args...]}` calls an actor. Ordinary sequences are data, and `{$literal: data}` suppresses evaluation of its entire payload. Quoting style and map field order do not select behavior.

This example is in [tests/cases/tutorial.yaml](tests/cases/tutorial.yaml):

```yaml
math: {$call: [import, 'std:math']}
prices: {$call: [list, 3, 5, 8]}
square_price: {$call: [fn, [price], {$call: [math.square, {$ref: price}]}]}
squared: {$call: [map, {$ref: square_price}, {$ref: prices}]}
valid: {$call: [assert, {$call: [eq, {$ref: squared}, {$call: [list, 9, 25, 64]}]}, unexpected squared prices]}
recovered: {$call: [try, {$call: [div, 1, 0]}, {$call: [fn, [error], {$call: [get, {$ref: error}, kind]}]}]}
```

Run it with `./manage.py run tests/cases/tutorial.yaml`. `squared` becomes
`[9, 25, 64]`, the passing assertion returns null, and `try` captures the division
error as `divide_by_zero`. Change a price without updating the assertion to see
a failure and its source location.

Legacy actor-first arrays are no longer executable syntax. Convert old source
explicitly with `./manage.py runtime migrate-syntax --write FILE` and review the
result. Without `--write`, the command reports files that would change; expected outputs and malformed or unsupported negative fixtures are excluded. Run
`./manage.py quality migration-audit --baseline REV` to compare migrated outcomes
with prior golden expectations. Review data and diagnostic changes in
`runtime/migration-audit/report.json` before blessing new expectations. The
runtime never guesses legacy syntax. JSON/YAML formatting tools may change
quoting, flow/block style or mapping presentation order without changing meaning.
Acyclic aliases and standard YAML tags are supported. Cyclic aliases, custom tags and non-string mapping keys remain unsupported.

See the [language reference](LANGUAGE.md) for syntax, builtins, types, imports,
and recovery.

## Immutable execution

Rust callers use `Vm::step(&self) -> Vm`, `run(&mut self)` and `inspect()`.
Helix programs create ready children with `{$call: [vm, {$ref: function}]}`, advance versions
with `{$call: [step, {$ref: child}]}`, and read state with `{$call: [inspect, {$ref: child}]}`. Earlier versions
keep their meaning when another branch advances. See
[`vm_parent_child.yaml`](tests/cases/vm_parent_child.yaml) for a complete example.
Functions retain direct code and lexical environments. Calls, iterations,
refinements, pure phases, resource budgets and cleanup are explicit machine state.
Function/machine pipeline results retain both their value projection and object table.

## Status

The longer-term aim is declarative machine configuration: describe and validate
a machine with types, then apply changes in a separate step. The pure type/class
algebra exists; applying machine changes does not. The [charter](CHARTER.md)
tracks that work and the suspend/resume design.

The language, Rust API, and saved-state format are experimental and may change.
Saved states have no format version or migration support yet. Keep source and
the runtime revision together when reproducibility matters. Recovery can retry
unfinished fields after an error, but cannot restore active calls or iterations.

## Development

`./manage.py` is the project tooling entrypoint. Run it without arguments to
browse commands, or append `--help` at any level.

Project help, suite/module discovery, builds, and tests default to concise text.
Help shows the current command level. Tests show suite outcomes, case counts,
skip reasons, and full failure diagnostics; successful stdout is suppressed, while
stderr warnings remain visible. Usage errors go to stderr with exit code 2.
Use `./manage.py --format=json` before a command for complete versioned reports,
including captured successful test output. `--help-json` requests the full command
catalog without building. This keeps routine agent context focused on decisions
while retaining explicit machine-readable evidence.
Language execution, module source, inventory, and
forwarded tools keep their own output contracts (YAML, raw pipeline data, or the
tool's chosen format).

```sh
python3 -m pip install PyYAML                # needed for tests and inventory
./manage.py test --describe                 # available suites and what they check
./manage.py test --suite golden --suite cli # a focused test run
./manage.py test                            # all suites
./manage.py lint                            # JSON lint report
./manage.py --format=json test --suite golden # complete machine-readable report
```

Lint uses the [hyperlinter](https://github.com/flintwinters/hyperlinter) submodule
and needs Git, Node.js, and npm; the command sets it up when needed.

- [Testing guide](tests/README.md): coverage, gaps, writing tests, and benchmarks.
- [AGENTS.md](AGENTS.md): architecture, invariants, and change discipline.
- [std/](std/): bundled libraries written in Helix; `./manage.py std` lists them.

The earlier C++ experiment lives in
[flintwinters/helixcpp](https://github.com/flintwinters/helixcpp).

### Change workflow

- Keep implementation and regression coverage in the same logical checkpoint.
  Extend existing suites through `./manage.py`; keep generated fixtures and
  artifacts in `runtime/`, never `/tmp`. Parallelize independent tests whenever
  possible; serialize only where shared state requires it.
- Put pure library laws in dogfood modules, exact output contracts in golden
  cases, and host/process contracts in Python suites. The [testing guide](tests/README.md)
  maps coverage and explains suite selection.
- Keep lint at zero diagnostics without weakening policy or adding suppression
  baselines. Prefer private or `pub(crate)` implementation and a small public
  API; reuse shared leaf modules without child-to-parent dependency cycles.
- Stage explicit paths only, never `git add .` or `git add -A`. Commit every
  verified logical checkpoint with a detailed message.

Use `./manage.py --help` or `--help-json` for command discovery,
`./manage.py test --describe` for suite contracts, and `./manage.py std [module]`
to discover embedded libraries and read their source.

## Usage log

Every runtime invocation made through `./manage.py` (`run`, `eval`, `exec`, `cli`,
`perform`, `check`, ...) is recorded in `runtime/usage.sqlite3` (table `calls`: command,
argv, source text or file contents, exit code, duration, caller). Analyse it with
`./manage.py inspect usage` (summary), `--schema`, or read-only `--sql 'SELECT ...'`.
Stdin programs are flagged but not captured. `HELIX_USAGE_LOG=0` disables logging,
`HELIX_USAGE_DB` relocates it, `HELIX_AGENT` labels the caller (`claude-code` is
inferred from `CLAUDECODE`). Test runs do not log, and the installed `helix` binary
or `target/` executables invoked directly bypass the log. The log is written by
`tooling/usage_log.yaml`, a Helix program using `std:sqlite` (the `sqlite` effect), so a
build is needed to log but not to report; a failed write only warns.

## Daily tooling

Install from this checkout with `cargo install --path . --locked`; Cargo puts
`helix` in its configured binary directory (normally `~/.cargo/bin`). The
`interactive` suite installs to a project-local directory and runs the result
outside the checkout. `helix --help` lists commands; `./manage.py --help-json`
describes project tooling. Successful runs exit 0, evaluation/validation failures
exit 1, and invalid arguments or unreadable input exit 2.

- `helix repl` evaluates independent YAML documents, one per line. Use `:begin`
  and `:end` for multiline input, `:cancel` to discard it, and `:quit` to leave.
- `helix fmt program.yaml` writes canonical source to stdout. It preserves
  the YAML representation and verifies graph equivalence; comments and presentation
  are normalized. Save to a separate file before replacing the original.
- `helix check program.yaml` parses and checks statically known builtin arities,
  including uncalled function bodies. It neither evaluates nor reads imports.
  Dynamic calls, imported definitions and value-dependent type errors remain
  runtime checks. `helix check --evaluate program.yaml` retains the earlier
  independent-error collection behavior. `./manage.py eval --check SOURCE`
  continues to select that evaluation behavior.
- `helix step program.yaml --steps 10` prints one inspection; `helix trace
  program.yaml --limit 100` streams inspections as YAML documents. Omit the
  limit to trace to a terminal status. These are observable engine transitions,
  not source-line breakpoints.
- `helix inspect state.yaml` summarizes a saved run or step inspection without
  executing it. Inspection snapshots are not disk-resumable continuations.
- `helix watch program.yaml` emits an initial run and reruns when source or
  transitive import contents change, including missing imports later created.
  `--interval-ms` controls polling and `--iterations` bounds run attempts (including unreadable-input attempts).
- `helix schema` emits the structural program schema; [editor setup](editor/README.md)
  explains its limits and the `helix lsp` stdio server.
- `helix completion bash` (or `zsh`, `fish`) prints a script to source in that
  shell; checked-in scripts live in `completions/`.

The corresponding project commands live under `./manage.py runtime`; saved
state inspection is `./manage.py inspect state FILE`. Verify these contracts
with `./manage.py test --suite static --suite interactive --suite inspection`.
