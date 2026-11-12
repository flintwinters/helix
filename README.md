# Helix

Helix is a small experimental runtime written in Rust. It reads YAML into an object graph, evaluates expressions over maps, lists, strings, numbers, and functions, and writes the resulting machine state back to YAML. This continues the experiment started in the [original C++ implementation](https://github.com/flintwinters/helixcpp).

The central idea is that program source and machine state should share one readable representation. You should be able to inspect a run, diff it, edit it, and eventually resume it as plainly as you edit source. Syntax, output, and tooling aim to be discoverable for both people and agents.

## Try it

You need Rust with Cargo (supporting the 2024 edition), Python 3.10 or newer, and [PyYAML](https://pypi.org/project/PyYAML/). The Python entrypoint imports PyYAML for all commands.

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

`./manage.py` is the single entrypoint for project tooling:

```sh
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

Linting uses the [hyperlinter](https://github.com/flintwinters/hyperlinter) submodule and requires Git, Node.js, and npm. The lint command initializes the submodule and installs its dependencies when needed.

The Rust crate exposes a library and a CLI. The source is organized around:

| Path | Responsibility |
| --- | --- |
| `src/graph.rs` | YAML parsing, object graph, parent links, and source spans |
| `src/eval.rs` | Evaluation, lexical scope, calls, and control flow |
| `src/builtins.rs`, `src/builtins/` | Builtin operations and shared value/error helpers |
| `src/vm.rs` | VM lifecycle and serialized state |
| `src/link.rs`, `src/library.rs`, `std/` | File imports and embedded Helix libraries |
| `src/emit.rs`, `src/error.rs` | Round-trippable YAML output and structured diagnostics |
| `tests/` | Programs paired with expected YAML states |

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

## Testing contracts and known gaps

All checks run through `./manage.py test`; no separate Python launchers are needed.
The default runs every suite. `HELIX_TEST_TIMEOUT` sets the per-interpreter timeout
in seconds (default 10). Fixtures and failure artifacts stay under this checkout's
ignored `runtime/` directory. Generated checks report fixed seeds; failures print
suite reproduction commands. Fuzz failures additionally retain minimized YAML and
its exact CLI command. Promote confirmed bugs to paired golden regression cases.

| Contract and exercised failure paths | Suite / authoritative fixtures | Known gaps |
| --- | --- | --- |
| Language results, structured errors, deterministic output, saved-root fixpoints | `golden`; `tests/cases/`, `tests/check/` when present | Not every builtin/type/arity combination has a case |
| Truncations and deletions produce structured states, no crashes | `robustness`; every golden source | Finite mutations are not a proof of panic freedom |
| Discovery pairs, invalid states, timeout/crash detection, wrong golden results, broken reload comparison | `harness`; synthetic fixtures and subprocess faults | Production mutation testing remains open |
| Usage errors, inaccessible files, run/check files versus stdin, stream and exit contracts | `cli` | No successful `--help` or shell completion yet |
| Integer expression trees versus Python, list sums/reversal laws, Unicode/nested literal save cycles | `generated`; seed 714209 | Bounded integers and exact binary fractions; general float/callable generation remains open |
| Shared import dependencies, cycles, missing/malformed files, isolation and imported blame | `imports`; generated file trees | Symlink/path-platform combinations not exhaustive |
| Independent VMs, repeated run, terminal failures, successful and failing check | `library`; `tests/vm.rs` | Ready states cannot be restored; suspension unavailable |
| Quoted/block scalars, nested lookup, duplicate-key rejection, unsupported YAML constructs, precise source blame | `parser` | Direct internal parent-link/path coverage remains open |
| Token mutations across parsing/linking/evaluation/emission, deletion minimization | `fuzz`; seed 901723 | Bounded 200 samples; not coverage-guided fuzzing |
| 1,001 / 4,001 / 10,001-node flat arithmetic maps | `scale` | No memory sampling or scaling regression baseline yet |

These are executable branch scenarios and failure-path contracts, not compiler
branch-coverage percentages. No instrumented Rust branch report exists yet; do not
infer exhaustive coverage from case counts. The largest scale workload measured
0.38 seconds locally (Linux x86-64, debug binary, including Python YAML decoding).
The suite requires the largest workload to finish within one second, in addition
to the subprocess timeout. The measurement is evidence for this
workload, not a universal runtime guarantee.

CI runs fast suites on pushes and pull requests; a weekly schedule and manual runs
include robustness, fuzz and scale checks. Failed runs upload `runtime/` artifacts.
The workflow itself needs a first hosted run to establish its environment timings.

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
suspended execution format or effect permissions are promised by this release.

## Changelog

### Unreleased

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
