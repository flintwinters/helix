# Testing Helix

Run commands from the repository root. Tests require Python 3.10+, PyYAML, and Rust with Cargo.
The `effects` suite also requires `curl`, `/bin/echo`, `/bin/sh`, and permission
to bind a local loopback HTTP listener. Its files and process fixtures stay under
the project's `runtime/` directory.
The `long_commands` suite requires Linux process groups and `/proc`, and exercises
real child processes with Python. It includes one operation lasting longer than
ten seconds; its derived test context allows twenty seconds per invocation.
The Unix public-host tests in `library` also use `/bin/sh` and `/bin/sleep` to
verify callback cleanup and cancellation through the Rust API.

```sh
./manage.py test --describe                  # discover suites and their contracts
./manage.py test --suite golden --suite cli  # select suites
./manage.py test --suite golden --case 'types_*'  # focus on a fixture family
./manage.py test                            # run all suites
```

For `golden` and `robustness`, repeat `--case GLOB` to select the union of matching
fixture stems or repository-relative paths (for example `tests/check/*.yaml`).
Quote globs to keep the shell from expanding them. Every pattern must match an
input; typos fail before building. Filters require explicit golden/robustness
suite selection and also restrict `--bless` to the selected golden expectations.
Selected cases retain discovery order and use the existing `--jobs` workers.

Test runs default to one line per suite and a final outcome/count summary.
Case counts, rewritten expectations, skip reasons, stderr warnings, and failing
suite diagnostics remain visible. Successful stdout is available with
`./manage.py --format=json test ...`, which emits the complete versioned JSON
report with per-suite status, failures, captured streams, and execution metadata.
Failure messages retain replay commands and artifact paths. Discovery is scoped
text by default; `--format=json` opts into structured discovery. Usage/selection
errors go to stderr and exit 2 in either format. Compilation failures report the
build phase before tests run.

## Test execution context

`manage.py` builds and snapshots the interpreter before running contracts.
`tooling/test_runner.py` owns fixture execution and the immutable `TestContext`
passed to each suite: repository root, executable, timeout, fixture directories,
and process callback. Override execution for a fixture or compiled mutant with
`dataclasses.replace(context, ...)`; never patch the entrypoint module or change a
shared executable setting. The context captures `HELIX_TEST_TIMEOUT` once per run.
Its process callback follows `subprocess.run` and supports isolated fault injection.

Golden-test decisions live in `std:test.golden_plan`. Python supplies captured
run output, saved expectations and ordinary-root replay observations; Helix
decides determinism, exact text comparison and replay outcomes. Python retains
independent state validation, process timeouts, crash detection and failure
artifacts. A policy crash or malformed response fails the suite; there is no
Python comparison fallback. The `harness` suite uses independent expected
outcomes and injected process faults to check this self-hosting boundary.

## Testing contracts and known gaps

All checks run through `./manage.py test`; no separate Python launchers are needed.
Golden and robustness cases use four workers by default; `--jobs N` controls case
concurrency (`--jobs 1` runs serially). Results retain discovery order. Suites remain
serial because some capture process-wide output or patch dependencies; robustness
variants stream per case.
The default runs every suite. `HELIX_TEST_TIMEOUT` sets the per-interpreter timeout
in seconds (default 10). Fixtures and failure artifacts stay under this checkout's
ignored `runtime/` directory. Generated checks report fixed seeds; failures print
suite reproduction commands. Fuzz failures additionally retain minimized YAML and
its exact CLI command. Promote confirmed bugs to paired golden regression cases.

| Contract and exercised failure paths | Suite / authoritative fixtures | Known gaps |
| --- | --- | --- |
| Explicit call/reference/literal forms, style-independent scalar meaning, canonical map order, standard tags and acyclic aliases | `yaml_model`; `tests/suites/yaml_model.py` | Cyclic aliases, custom tags and non-string keys are deliberately unsupported |
| Language results, structured errors, deterministic output, saved-root fixpoints | `golden`; `tests/cases/`, `tests/check/` | Not every builtin/type/arity combination has a case |
| First-class type regions, phase-local errors, run/check parity, file-input exclusion and bounded evaluation | `type_time`; `tests/suites/type_time.py` plus type golden cases | File imports are conservatively excluded from programs using explicit type regions |
| Truncations and deletions produce structured states, no crashes | `robustness`; every golden source | Finite mutations are not a proof of panic freedom |
| Discovery pairs, invalid states, timeout/crash detection, wrong golden results, broken reload comparison | `harness`; synthetic fixtures and subprocess faults | Compiled faults are checked by `mutants` |
| Usage errors, inaccessible files, run/check files versus stdin, discovery, and recovery | `cli` | Language and project CLIs support successful help; no shell completion |
| Inline/stdin programs, raw string output, YAML round trips, structured stderr and shell pipes | `pipeline`; `tests/suites/pipeline.py` | No shell completion or platform-wide shell matrix |
| Function declaration locations, imported call chains, caught errors and re-raised causes | `errors`; `tests/suites/errors.py` | Pure recovery; no effect or suspended-call diagnostics |
| Integer expression trees versus Python, list sums/reversal laws, Unicode/nested literal save cycles | `generated`; seed 714209 | Bounded integers and exact binary fractions; general float/callable generation remains open |
| YAML toolkit scalar types, block/flow syntax, Unicode/controls, rejection cases, deterministic mutations, nested paths and parser bounds | `dogfood`; `tests/dogfood/yaml/` | Helix data subset supports bounded acyclic aliases and standard tags; cyclic aliases, custom tags and non-string keys are rejected |
| Helix assertions, bounded collection laws, nested reports, runner failures | `dogfood`; `tests/dogfood/` | Explicit finite samples; no automatic random generation or shrinking |
| Shared import dependencies, cycles, missing/malformed files, isolation and imported blame | `imports`; generated file trees | Symlink/path-platform combinations not exhaustive |
| Independent VMs, repeated run, terminal failures, successful and failing check | `library`; `tests/vm.rs` | Disk restoration is not implemented |
| Quoted/block scalars, nested lookup, duplicate-key rejection, unsupported YAML constructs, precise source blame | `parser` | Parent-link consequences checked through nested lexical lookup and precise paths/spans; no graph-internal API exposed |
| Token mutations across parsing/linking/evaluation/emission, deletion minimization | `fuzz`; seed 901723 | Bounded 200 samples; not coverage-guided fuzzing |
| 1,001 / 4,001 / 10,001-node flat arithmetic maps | `scale` | `memory` adds peak RSS and broad scaling thresholds; no historical statistical baseline |
| Every registered actor: independent result witness, declared arity boundaries, applicable wrong types and error blame | `semantics`; `tests/suites/semantics.py`; `runtime/coverage/actors.json` | Finite scenarios; unrestricted operands have no wrong-type case; not every argument-position/type cross-product |
| Captures, recursion, nested callbacks, lazy recovery and memoization composed together | `dogfood`; `tests/dogfood/interactions.yaml` | Pure recovery only; no suspended continuations |
| Nested lists/maps/records/unions versus independent Python membership, invalid declarations, ordered violations, diamonds and provenance | `type_algebra`; `tests/suites/type_algebra.py` | Finite implemented fragment; section 8 still owns stronger algebra and proof obligations |
| Immutable steps versus run, retained pause points, nested machines, type phases, constraints, unwinding and host payload allocation growth | `execution`; `tests/execution.rs`, `tests/execution_memory.rs` | Named inspection is not a disk snapshot format; allocation checks bound growth rather than allocator-specific fixed overhead |
| Accepted/rejected syntax, graph, recursion, iteration and computed-growth boundaries | `resources`; computed-growth golden cases; `library` | Budgets are not a byte-exact heap quota; source reads precede limits |
| Embedded-only and confined file imports, traversal, symlinks, transitive and uncalled imports | `permissions`; `library` | Path authorization assumes no hostile concurrent directory replacement |
| Host file, environment, stream, command, HTTP, clock and random effects; capability denial, deterministic runs and symlink escapes | `effects`; `tests/cases/effects_world.yaml` | Uses local HTTP and subprocess adapters; grants do not provide OS isolation |
| Extended deadlines, cumulative output bounds, metadata progress, classified retries, SIGINT/SIGTERM cancellation and descendant liveness | `long_commands`; `tests/suites/long_commands.py` | Linux local synchronous commands; no SIGKILL recovery, detached-process containment or remote execution |
| Pending effects, completion records, failure cleanup, external interruption and resumption without repeating completed requests | `execution`; `tests/execution.rs` | In-memory continuations only; retained branches require explicit host handling and are not globally deduplicated |
| Actual compiled arithmetic, source-column and serialization faults rejected by existing suites | `mutants`; `runtime/mutants/` | Three targeted mutations, not a general mutation score |
| Growing graphs and constant-state 999/99,999-step loops, interpreter peak RSS and environment | `memory`; `runtime/memory/` | Linux + GNU time; broad operational thresholds, not a universal memory bound |

These are executable branch scenarios and failure-path contracts, not compiler
branch-coverage percentages. No instrumented Rust branch report exists yet; do not
infer exhaustive coverage from case counts. The scale suite measures debug CLI execution including Python YAML decoding.
It requires the largest workload to finish within one second, in addition
to the subprocess timeout. The measurement is evidence for this
workload, not a universal runtime guarantee.

CI runs selected suites on pushes and pull requests; a weekly schedule and manual runs
execute all suites, including `type_time`, `errors`, robustness, fuzz, scale, memory, and mutants. Failed runs upload `runtime/` artifacts.
Local timing evidence does not establish hosted CI timing guarantees.

## Dogfooded test modules

`./manage.py test --suite dogfood` discovers `tests/dogfood/**/*.yaml`.
Each module exports a `report` built with `std:test`. Assertions, tables,
properties, and nested failure aggregation run inside Helix; Python discovers
files, checks process/state integrity, and saves replayable inputs and states
under `runtime/dogfood/`. This suite runs in fast CI and the default test run.

Start a new module with this scaffold (no expected-state file is needed):

```yaml
t: [import, std:test]
report:
  - t.suite
  - - {name: addition, run: [fn, [], [t.equal, [add, 2, 3], 5]]}
    - {name: division_error, run: [fn, [], [t.throws, [fn, [], [div, 1, 0]], "divide_by_zero"]]}
```

The runner applies `t.require_pass` to the exported report, so captured failures
fail the command while retaining later case results and nested error data.
See `tests/dogfood/collections.yaml` for bounded property samples and composition
of child reports. Use `[dict, "input", variable]` to put evaluated values into
case records; a plain scalar map value is literal data. Keep host-specific tests
in the Python contract suites and exact serialization contracts in golden cases.
The collection laws exhaust all 40 lists of length zero through three over
`{-1, 0, 1}`, including 200 split boundaries and an independent counting oracle
for sorting. Type laws cross scalar/container samples with primitive and constructed
types, check union membership, record derivation, and independently captured closures.
These are deterministic finite checks, not exhaustive language verification.

The bridge self-tests failing assertions, continued execution, missing reports,
and malformed modules on every dogfood run.

## Failure artifacts

Golden and robustness failures identify the source path and contract stage, show a
rerun command, and save complete output evidence under `runtime/test-failures/`.
Golden mismatches include a bounded unified diff. Round-trip and damaged-input
failures save their exact replay source; `check` replays retain check semantics.
Evidence for the same case and stage is replaced on the next failure.

## Resource checks and benchmarks

`./manage.py test --suite resources --suite permissions --suite library` verifies accepted/rejected syntax and graph boundaries, call and iteration caps, growing values, dependency/import depth, source blame and permission escapes. The public VM tests also exercise guarded execution on Rust test threads, whose smaller stacks previously exposed a host stack overflow. These guards bound interpreter work and produced data; they do not promise constant memory for programs retaining growing state.

`semantics` records successful scenario counts per actor and rejects missing witnesses when the builtin registry changes. `type_algebra` checks independent membership judgments over nested descriptions and mixed values, plus ordered violations and class diamonds. This is evidence for the implemented structural fragment; section 8 retains normalization, recursive types and formal proof obligations.

`memory` requires Linux and GNU `/usr/bin/time`. It measures only interpreter peak RSS (KiB), records platform/Python/debug-profile metadata, and saves exact sources and states. Workloads use 1,001/4,001/10,001-node arithmetic maps (each under two seconds and 192 MiB), with a broad 12x-plus-16-MiB scaling allowance, and compare 999 versus 99,999 constant-state iterations (at most 8 MiB extra RSS). The existing `scale` suite retains its one-second 10k-node bound. These tolerances catch large regressions while allowing allocator and host noise; they are not a historical statistical baseline. Linux CI runs these measurements and compiled mutation checks in the weekly/manual full sweep and uploads the reports even on success.

Speed benchmarks run through `./manage.py test --suite speed` and the default full
suite. They measure the debug CLI (startup, loading, evaluation and emission),
excluding compilation and Python YAML decoding. Each workload gets one warmup
and five measured runs, checked against its expected result. Arithmetic maps,
function loops, ASCII/Unicode slicing, and long-key map reads/replacements save
raw samples, median/range, throughput, platform and binary hash to
`runtime/speed/report.json`; unique run directories retain inputs and states.
Reports flag `SLOWDOWN` when median time exceeds the earliest five compatible runs
by more than 20% and three median absolute deviations of observed noise. Matching
host/environment, sampling settings and workload source keep comparisons relevant;
the fixed early baseline reveals cumulative drift. First runs establish history.
Flags are observational and do not fail the suite. Normal interpreter timeouts
still apply. The weekly/manual full CI sweep includes this suite.

Workload epochs in `tests/suites/speed.py` separate fixed historical baselines.
Advance only the affected epochs after measured improvements, and retain the
historical reports.

Memory and compiled-mutation artifacts use unique per-run directories. Their root `report.json` files identify the latest run directory; concurrent runs cannot replace each other's inputs, RSS files or compiled binaries. Successful mutation runs remove their build target after retaining source/control logs.

Immutable execution profiles record run/step/checkpoint allocation counts and timings in `runtime/execution-profile.log`. Ordinary-data roots must remain emission fixpoints, including scalars and empty containers. Function and machine values use named object graphs and are checked through inspection and execution contracts rather than source reconstruction.

Daily tooling contracts are grouped into `static` (non-executing diagnostics,
source-format equivalence/idempotence, schema and LSP framing), `interactive`
(REPL, transitive/missing import watch changes, completions, local Cargo install),
and `inspection` (bounded stepping, trace parity and observational saved-state
validation). Their source witnesses live in `tests/cases/`; subprocess artifacts
stay in `runtime/`. Evaluation golden fixtures under `tests/check/` explicitly
use `check --evaluate`; plain `check` never evaluates a program.

Resource regressions grow function and machine capture graphs from shallow source until an enforced limit. They must emit structured errors and exit cleanly when releasing the retained graph. Public execution tests also release captured graphs on a small-stack thread and through thread-local teardown, preserving retained machine versions.
