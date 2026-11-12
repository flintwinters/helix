# Helix

Helix is a small experimental runtime written in Rust. It reads YAML into an object graph, evaluates expressions over maps, lists, strings, numbers, and functions, and writes the resulting machine state back to YAML. This continues the experiment started in the [original C++ implementation](https://github.com/flintwinters/helixcpp).

The central idea is that program source and machine state should share one readable representation. You should be able to inspect a run, diff it, edit it, and eventually resume it as plainly as you edit source. Syntax, output, and tooling aim to be discoverable for both people and agents.

## Try it

For shell pipelines, build with `./manage.py build` and put `target/debug` on your
`PATH` (or install the executable with `cargo install --path .`). Then:

```sh
helix exec '"[add, 20, 22]"' | helix exec
# 42
helix exec '[add, 20, 22]'
# 42
```

`helix exec [PROGRAM | -]` evaluates inline YAML, or reads a program from stdin
when its program argument is omitted. It emits string results verbatim, without added quotes or a
newline, and other values as YAML using the normal round-trip emitter. A string
containing program source therefore becomes executable input to the next `helix exec`.
Errors go to stderr as structured VM state, leave stdout empty, and exit 1;
usage/read errors exit 2. Use `set -o pipefail` to detect failures anywhere in a
shell pipeline. Imports resolve from the working directory. `helix run` continues
to emit full VM state for inspection and recovery.

`helix` is the single language executable: `exec`, `run`, `check`, `recover`,
`resume`, `sandbox`, and `std` share the same dispatcher and VM. Run `helix --help`
to discover them. During development, `./manage.py runtime exec PROGRAM` builds
and invokes the same pipeline command; omitting `PROGRAM` reads stdin.

You need Rust with Cargo (supporting the 2024 edition), Python 3.10 or newer, and [PyYAML](https://pypi.org/project/PyYAML/). Help, command discovery, builds, and runtime invocation work without PyYAML; tests and inventory use it.

```sh
python3 -m pip install PyYAML
./manage.py build
./manage.py run tests/cases/arithmetic.yaml
```

That example is a complete program:

```yaml
x: 2
y: [add, x, 1]
z: [mul, [sub, y, 1], 5]
```

Lists with an actor first are expressions. Here, `y` refers to `x`, and `z` refers to `y`. Running it prints:

```yaml
status: finished
root:
  x: 2
  "y": 3
  z: 10
```

The emitter quotes strings where needed to preserve their meaning when read again. The evaluated `root` can itself be run as a program; the test suite checks that finished roots remain stable across this round trip.

Use `./manage.py run -` to read a program from stdin. After building, the executable is also available as `target/debug/helix run FILE`.

## What works today

The runtime supports lexical name resolution, arithmetic and collections, lazy conditionals, local bindings, user functions and recursion, iteration, file imports, embedded standard libraries, and structured errors with source locations. Unquoted strings inside expressions resolve as names; quoted strings are literals. Values are immutable.

```yaml
math: [import, std:math]
answer: [math.square, 6]
double: [fn, [n], [mul, n, 2]]
results: [map, double, [1, 2, 3]]
```

Execution currently runs to completion or error. Suspend/resume remains a design goal: a machine promising reversibility must expose all state needed to restore its execution, including calls, locals, continuations, and pending effects. That promise is not implemented yet.

The longer-term direction is declarative machine configuration through first-class types: describe and validate a machine before applying changes to it. See the [charter](CHARTER.md) for the roadmap and the [language reference](LANGUAGE.md) for documented syntax and semantics.

## Development

`./manage.py` is the single entrypoint for project tooling. Commands form a tree:
`runtime` evaluates programs and recovery state, `inspect` discovers modules and source,
and `quality` runs tests and lint. `build` compiles the crate directly. Bare commands
and groups show help; append `--help` at any level for arguments and examples.
Existing flat commands remain equivalent shortcuts.

```sh
./manage.py                         # show the command tree
./manage.py runtime --help          # discover execution commands
./manage.py runtime eval '[add, 20, 22]'
./manage.py inspect std math
./manage.py quality test --describe # suite names and contracts, without building
./manage.py quality test --suite cli
./manage.py --help-json             # versioned JSON tree, arguments, shortcuts, suites
```

Help, JSON discovery, and suite listings require only Python's standard library.
Tests and inventory also require PyYAML. Runtime commands build automatically.
All paths resolve from the repository root, including when invoked from another directory.
Runtime output stays on stdout; exit codes are 0 for success, 1 for evaluation/test
failure, and 2 for invalid usage or unreadable input. Build and forwarded tools retain
their own exit codes. `cli --help` and `lint --help` describe the wrapper;
use `cli -- --help` or `lint -- --help` to pass help to the underlying tool.

Common shortcuts:

```sh
./manage.py eval '[add, 20, 22]'  # evaluate inline YAML; emits state, exits 0/1
./manage.py eval --check 'x: [add, missing, 1]'
./manage.py cli std math      # build and forward CLI arguments verbatim
./manage.py build             # compile the Rust crate
./manage.py run FILE          # evaluate and emit YAML state
./manage.py std               # list embedded standard modules
./manage.py std math          # print a module's Helix source
./manage.py test              # all regression and bounded generated suites
./manage.py test --list       # discover suites
./manage.py test --suite golden --suite cli  # select suites (repeat --suite)
./manage.py test --bless      # deliberately regenerate golden expectations
./manage.py lint --format=text
```

Golden and robustness failures identify the source path and contract stage, show a
rerun command, and save complete output evidence under `runtime/test-failures/`.
Golden mismatches include a bounded unified diff. Round-trip and damaged-input
failures save their exact replay source; `check` replays retain check semantics.
Evidence for the same case and stage is replaced on the next failure.


Linting uses the [hyperlinter](https://github.com/flintwinters/hyperlinter) submodule and requires Git, Node.js, and npm. The lint command initializes the submodule and installs its dependencies when needed.

The Rust crate exposes a library and one executable (`helix`). The source is organized around:

| Path | Responsibility |
| --- | --- |
| `src/graph.rs` | YAML parsing, object graph, parent links, and source spans |
| `src/eval.rs` | Evaluation, lexical scope, calls, and control flow |
| `src/builtins.rs`, `src/builtins/` | Builtin operations and shared value/error helpers |
| `src/vm.rs` | VM lifecycle and serialized state |
| `src/link.rs`, `src/library.rs`, `std/` | File imports and embedded Helix libraries |
| `src/emit.rs`, `src/error.rs` | Round-trippable YAML output and structured diagnostics |
| `src/main.rs` | Unified language CLI: execution, pipelines, checking, recovery and module discovery |
| `tests/` | Golden states, Helix test modules, Python contracts, and Rust VM tests |

Read [AGENTS.md](AGENTS.md) for architectural invariants and change discipline.

## A short tutorial

Save the following as `tutorial.yaml` (also checked as `tests/cases/tutorial.yaml`):

```yaml
math: [import, std:math]
prices: [list, 3, 5, 8]
square_price: [fn, [price], [math.square, price]]
squared: [map, square_price, prices]
valid: [assert, [eq, squared, [list, 9, 25, 64]], "unexpected squared prices"]
recovered: [try, [div, 1, 0], [fn, [error], [get, error, "kind"]]]
```

Run `./manage.py run tutorial.yaml`. Under `root`, `squared` is `[9, 25, 64]`,
`valid` is null (the assertion passed), and `recovered` is `divide_by_zero`.
`math` imports a bundled module, `square_price` is a function, and `map` calls
that function for each price. Change the prices and the expected squares together
to adapt the program. Change only a price to see an assertion failure: the state
contains its message and source location, and the command exits 1. Inside expressions,
quote literal strings; leave variable names unquoted. `try` converts the division
failure into readable data rather than aborting this program.

Save command output to inspect it later: `./manage.py run tutorial.yaml > state.yaml`.
The output is a state envelope; to rerun a finished result, use its `root` value as
the program. Loading the whole envelope does not resume execution. Imports resolve
relative to the source file; stdin imports resolve relative to the working directory.
For reproducible dependencies, commit library sources under `vendor/` and import
their entry files by relative path; see [pinned dependencies](LANGUAGE.md#pinned-dependencies).

## Testing contracts and known gaps

All checks run through `./manage.py test`; no separate Python launchers are needed.
The default runs every suite. `HELIX_TEST_TIMEOUT` sets the per-interpreter timeout
in seconds (default 10). Fixtures and failure artifacts stay under this checkout's
ignored `runtime/` directory. Generated checks report fixed seeds; failures print
suite reproduction commands. Fuzz failures additionally retain minimized YAML and
its exact CLI command. Promote confirmed bugs to paired golden regression cases.

| Contract and exercised failure paths | Suite / authoritative fixtures | Known gaps |
| --- | --- | --- |
| Language results, structured errors, deterministic output, saved-root fixpoints | `golden`; `tests/cases/`, `tests/check/` | Not every builtin/type/arity combination has a case |
| First-class type regions, phase-local errors, run/check parity, file-input exclusion and bounded evaluation | `type_time`; `tests/suites/type_time.py` plus type golden cases | File imports are conservatively excluded from programs using explicit type regions |
| Truncations and deletions produce structured states, no crashes | `robustness`; every golden source | Finite mutations are not a proof of panic freedom |
| Discovery pairs, invalid states, timeout/crash detection, wrong golden results, broken reload comparison | `harness`; synthetic fixtures and subprocess faults | Compiled faults are checked by `mutants` |
| Usage errors, inaccessible files, run/check files versus stdin, discovery, and recovery | `cli` | Language and project CLIs support successful help; no shell completion |
| Inline/stdin programs, raw string output, YAML round trips, structured stderr and shell pipes | `pipeline`; `tests/suites/pipeline.py` | No shell completion or platform-wide shell matrix |
| Function declaration locations, imported call chains, caught errors and re-raised causes | `errors`; `tests/suites/errors.py` | Pure recovery; no effect or suspended-call diagnostics |
| Integer expression trees versus Python, list sums/reversal laws, Unicode/nested literal save cycles | `generated`; seed 714209 | Bounded integers and exact binary fractions; general float/callable generation remains open |
| YAML toolkit scalar types, block/flow syntax, Unicode/controls, rejection cases, deterministic mutations, nested paths and parser bounds | `dogfood`; `tests/dogfood/yaml/` | Helix data subset; aliases, explicit tags and container keys rejected; no claim of full YAML conformance |
| Helix assertions, bounded collection laws, nested reports, runner failures | `dogfood`; `tests/dogfood/` | Explicit finite samples; no automatic random generation or shrinking |
| Shared import dependencies, cycles, missing/malformed files, isolation and imported blame | `imports`; generated file trees | Symlink/path-platform combinations not exhaustive |
| Independent VMs, repeated run, terminal failures, successful and failing check | `library`; `tests/vm.rs` | Ready states cannot be restored; suspension unavailable |
| Quoted/block scalars, nested lookup, duplicate-key rejection, unsupported YAML constructs, precise source blame | `parser` | Parent-link consequences checked through nested lexical lookup and precise paths/spans; no graph-internal API exposed |
| Token mutations across parsing/linking/evaluation/emission, deletion minimization | `fuzz`; seed 901723 | Bounded 200 samples; not coverage-guided fuzzing |
| 1,001 / 4,001 / 10,001-node flat arithmetic maps | `scale` | `memory` adds peak RSS and broad scaling thresholds; no historical statistical baseline |
| Every registered actor: independent result witness, declared arity boundaries, applicable wrong types and error blame | `semantics`; `tests/suites/semantics.py`; `runtime/coverage/actors.json` | Finite scenarios; unrestricted operands have no wrong-type case; not every argument-position/type cross-product |
| Captures, recursion, nested callbacks, lazy recovery and memoization composed together | `dogfood`; `tests/dogfood/interactions.yaml` | Pure recovery only; no suspended continuations |
| Nested lists/maps/records/unions versus independent Python membership, invalid declarations, ordered violations, diamonds and provenance | `type_algebra`; `tests/suites/type_algebra.py` | Finite implemented fragment; section 8 still owns stronger algebra and proof obligations |
| Accepted/rejected syntax, graph, recursion, iteration and computed-growth boundaries | `resources`; computed-growth golden cases; `library` | Budgets are not a byte-exact heap quota; source reads precede limits |
| Embedded-only and confined file imports, traversal, symlinks, transitive and uncalled imports | `permissions`; `library` | Path authorization assumes no hostile concurrent directory replacement; future effects need their own adapters |
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

## Repository inventory dogfood

Run `./manage.py inventory` to list runtime, library, and tooling source files grouped by directory, with counts.
It includes tracked and unignored untracked files under `src/`, `std/`, and `tooling/`,
excluding submodule contents. The scope stays small for the current copying-based grouping algorithm. Python observes paths; `tooling/inventory.yaml` builds the report using
`std:group.by`. Grouping policy runs in Helix and failures fail the command.
This auxiliary command does not participate in building the interpreter.

The command saves `runtime/inventory.input.yaml` and `runtime/inventory.state.yaml`.
Replay the observation with `./manage.py run runtime/inventory.input.yaml` from
this checkout. The golden `dogfood_tooling_inventory` case and the `cli` contract
suite cover the shared shim and host bridge.

## Stability and compatibility

Helix is experimental, before a stable language or persisted-state format release.
The current behavioral contracts are readable YAML, structured errors with source
blame, lexical scope, deterministic pure evaluation and finished-root round trips.
Changes to these contracts require regression cases and documentation. Golden
fixtures freeze the currently expected behavior for development; they do not
promise cross-version resume compatibility.

Builtin names, type/class declarations, the Rust API and exact error prose may
change before a stable release. Error kinds and field shapes are reviewed as public
behavior. The saved-state envelope has no format version or migration support yet;
keep source and the runtime revision together when reproducibility matters. No
suspended execution format is promised. Import permissions are available; other effects remain unimplemented.

## Changelog

### Unreleased

- Added `helix exec` for raw-result shell pipelines, discoverable project commands, pure recovery/retry, type-time regions, and YAML data helpers.
- Added guarded runtime execution, import confinement, actor/arity census, nested-type oracles, composed feature tests, compiled mutation checks and Linux peak-RSS regressions.

- Added selectable contract suites, subprocess timeouts, harness self-tests and
  direct public VM tests behind `./manage.py test`.
- Added fixed-seed integer/library oracles, repeated literal serialization checks,
  generated import trees, parser contracts and bounded fuzzing with minimized
  failure artifacts.
- Added repeatable graph-size workloads and fast/weekly CI configuration.
- Documented compatibility limits, testing gaps and a golden-backed tutorial.

### 0.1.0 development baseline

- YAML object graphs, lexical evaluation, arithmetic, collections, functions,
  iteration, structured recovery and file/standard-library imports.
- Golden states, deterministic evaluation and saved-root round-trip checks.

Use `git log` for the complete implementation history; this changelog records
user-visible checkpoints rather than claiming published releases.

## Runtime trust boundaries

Run `./manage.py runtime sandbox - program.yaml` to allow embedded modules only, or replace `-` with an allowed import directory. The main input is explicitly authorized; transitive file imports must stay within that directory, including through symlinks. Ordinary `run` remains trusted. See `LANGUAGE.md` for the library API, exact resource budgets and the limits of path-based confinement.

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
