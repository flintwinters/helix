# Helix

Helix is an experimental runtime in which readable YAML is both program source and persisted VM state. Programs are object graphs, vector forms such as `[add, x, 1]` are executable expressions (actor first), and completed results and structured failures serialize back to YAML. Full suspended execution is a future goal.
Outputs and syntax should be agent-first and agent-friendly, and all APIs and tooling should be discoverable.

Keep bootstrapping single-step: Helix may support auxiliary tooling, but must not become a prerequisite for building itself.

The previous C++ implementation lives in `flintwinters/helixcpp`.

Fix bugs without asking.

## Motivation

Program and machine state share one human-readable representation, so a suspended run can be read, diffed, edited, and resumed as plainly as source. Every design choice should keep that round trip lossless and legible.

The ultimate niche is declarative machine configuration (where Nix and Ansible sit), reached through declarative, first-class types: a machine config is a type resolved entirely at type time, inheriting from abstract machine type classes, and applying it is a separate effectful step (`CHARTER.md` section 8). The pure type/class algebra and state comparison exist; effectful machine application does not.

## Architecture

Rust crate `helix` exposes a public VM library, the state-oriented `helix` CLI, and the pipeline executable `hx`. `./manage.py` is the single project-tooling entrypoint; its discoverable command tree is authoritative.

- `src/graph.rs`: YAML (via `yaml-rust2` events) into an arena of nodes with parent links and 1-indexed line/column spans; root-relative paths (`/inner/y/1`); parent links support lexical resolution through enclosing maps.
- `src/vm.rs`: `Vm` with the five statuses and `state()` serialization (`status` plus `root` or `error`), around an `Evaluator`. `Vm::check` / `helix check` collect independent errors with the same pure evaluator, emitting `phase: type` and `errors`; `tests/check/` holds check-command golden cases. This is evaluation, not static checking of uncalled bodies.
- `src/eval.rs`: memoized evaluation, cycle detection, lexical scope, call frames, closures, and forms with their own evaluation order. Plain-string expression arguments are symbols; quoted strings are literals. Builtins, named functions, and computed functions can be call heads. Function source and captured lexical bindings survive emitted-state round trips; calls have per-frame evaluation and bounded recursion.
- `src/builtins.rs` (+ `builtins/{number,collection,fault,value}.rs`): pure builtins over evaluated values, with the arity table. `fault` and `value` are the shared leaf modules (children must not import their parent: hyperlinter treats that as a cycle). Failures are `Fault`s the VM maps onto the blamed argument node. The user-facing semantics live in `LANGUAGE.md`; keep it in sync when builtins change.
- `src/builtins/typing/`: type parsing, membership/subtyping, constructors/reflection, class inheritance, and pure machine-state comparison. Types are primitive names or ordinary declaration maps; refinements use the evaluator’s function machinery. `std:types` implements reflection-based record derivation in Helix over the existing checked constructors.
- `[type_time, expression]` uses the ordinary evaluator with phase-local memoization and shared resource budgets. It admits embedded modules and supplied source only; any file import in the linked program causes a catchable `effect` error on entry. Errors carry `phase: type`, including through `try`; uncaught failures also mark VM state. Future effect actors must enforce this boundary before acting. `type_time` suite covers phase isolation, run/check parity and limits.
- `src/link.rs` links literal imports at load time into detached graph subtrees, deduplicated by canonical file path or standard module name, with cycle detection. Module scope is isolated; dotted symbols traverse map/module members. `src/library.rs` embeds `std/` Helix sources; `./manage.py std [module]` discovers them. Imported errors carry `file` with file-relative paths and spans.
- `std/math.yaml` and `std/list.yaml` exercise function parameter/result annotations in library code. Numeric contracts share `std:math.number`; library contract regressions cover imports and emitted-state round trips.
- `std/test.yaml` owns pure testing policy (case capture, assertions, error expectations, tables, bounded properties, differential checks, finite sample generators, selection/reruns and report aggregation). `dogfood_testing` self-tests failures, selection/reruns, fixture closures and nested reports; `manage.py` retains host process/crash and filesystem duties.
- `tests/dogfood/**/*.yaml` exports `std:test` reports; `./manage.py test --suite dogfood` discovers modules and enforces `require_pass`, saving replay inputs/states in `runtime/dogfood/`. Add pure library laws here without golden snapshots; retain host/process contracts in Python.
- `std/yaml.yaml` exposes data-only parse/dump/valid and immutable nested path helpers. `src/builtins/yaml.rs` reuses the graph parser (whole-stream, single-document mode) and VM emitter; parsed lists never execute. Toolkit laws live in `tests/dogfood/yaml.yaml` and `tests/dogfood/yaml/`.
- `src/emit.rs`: Helix's own block-YAML emitter. It exists so output stays re-readable: it quotes strings that would re-read as another type or, as the first item of a list, as an expression head.
- Pure error recovery: `./manage.py recover FILE` emits editable recovery source on evaluation failure; `resume STATE` preserves completed root-map fields and retries unfinished fields. It does not restore active calls or iterations. Ordinary run/check state output stays unchanged. Error `calls` records innermost-first call sites and separate function declaration files/coordinates; map re-raise preserves `cause`.
- `src/error.rs`: `HelixError` with stable `kind`, message, root-relative `path`, and `line`/`column`; serialized under `error:`.
- Auxiliary tooling: `./manage.py inventory` observes repository paths in Python and calls `tooling/inventory.yaml` for grouping/report policy through `std:group`. Helix stays outside the build bootstrap; replayable input and VM state live in `runtime/`.
- Tests: golden cases in `tests/cases/NAME.yaml` + `NAME.expected.yaml`; finished states are also checked to round-trip (emitted `root` re-run is a fixpoint).
  A robustness sweep re-runs damaged variants of every case (line prefixes, line deletions, char prefixes) and requires a well-formed state: exit 0/1, valid YAML, errors with `kind`/`message`/`line`/`column`.
  Discover suites with `./manage.py test --list`; repeat `--suite NAME` to select. Python contracts in `tests/suites/` cover the harness, CLI, generated oracles/serialization, imports, parser, fixed-seed fuzzing and scale; `library` invokes Rust public-VM tests. Subprocesses have a 10-second timeout (`HELIX_TEST_TIMEOUT` override). Generated files and minimized failures belong in `runtime/`. README maps contracts to suites and records coverage gaps; CI runs fast checks on changes and the full sweep weekly.

- Lint: `hyperlinter/` is a submodule (`flintwinters/hyperlinter`); `./manage.py lint [--format=text]` runs it from the repo root (it loads Cargo.toml and `.git/hyperlinter/` baselines there). Its append-only ledger lives beside baselines in private Git metadata (`hyperlinter/hyperlint.sqlite`); legacy runtime ledgers migrate without copying when safe, removing only empty directories. Keep the tree at zero diagnostics; do not weaken policy or write a baseline to silence findings. The Rust adapter models each `mod` file as its own module and leaves encapsulation to rustc (entrypoint rules HL108/HL109 do not apply; HL117 bounds `mod` fan-out). Function cycles, dead implementation, and clone rules are still TypeScript-only. Rust style here: private `mod`s with a small `pub use` surface in `src/lib.rs` where it reads well, otherwise ordinary `pub mod` paths; prefer `pub(crate)`/private over `pub` for items no other crate needs.

## Current Big Tasks

The human-facing roadmap and daily-driver checklist is `CHARTER.md`; tick items there only when they work end to end with a golden case. The list below is the immediate agent queue.

1. Suspend/resume through a reversibility promise type: a machine mixing in that promise must expose all behavior and complete execution state for serialization, including stack, heap, locals, continuations and pending effects as needed. Execution references remain root-relative paths. The promise and suspend/resume are not implemented; today the VM runs to completion or error in one `run`.
2. Add capability-controlled effects and signals beyond imports. The pure core, first-class functions/types, structured recovery, and assertions already exist; effectful machine application remains future work.
3. Preserve source locations in the output for evaluated values, not only for errors.
4. Exercise hyperlinter as the Rust code grows and feed general (non-Helix-specific) gaps back upstream: Rust function-cycle/dead-code coverage.
5. Decide the fate of YAML anchors/aliases and non-scalar keys (currently rejected as `unsupported`).

## Invariants

- YAML shape is a public behavioral contract: preserve existing source blame, structured error details, and round-trippable state. Evaluated-value source annotations remain future work.
- Reversibility is a promise type: a machine mixing it in must expose all behavior and complete state so serialization can always capture and restore its execution. Hidden host state cannot determine its continuation.
- Persist resumable execution references as root-relative object paths, never opaque pointers.
- VM statuses are `ready`, `running`, `finished`, `error`, or `signaled`.
- Parent links define lexical/object resolution.

## CLI Quick Reference

- `hx [PROGRAM | -]` is the pipeline executable: inline source or stdin, raw string results (no added newline), other results through the shared YAML emitter. Evaluation failures emit VM state only on stderr and exit 1; usage/read failures exit 2. `helix run` retains its state envelope. Verify with `./manage.py test --suite pipeline`.

- `./manage.py` shows the command tree: `build`, `runtime` (execution/recovery), `inspect` (modules/inventory), `quality` (test/lint). Append `--help` at any level; flat commands remain equivalent shortcuts.
- `./manage.py test --suite startup` checks discovery import boundaries without compiling or running Helix.
- `./manage.py --help-json` emits the versioned command/argument tree and suite contracts without builds or third-party dependencies. `quality test --describe` explains suites; `--list` preserves name-only discovery. Parser definitions and suite descriptions live in `tooling/cli.py`; keep that authoritative.
- `./manage.py runtime eval '[add, 20, 22]'` builds and emits ordinary VM state; `eval --check SOURCE` collects independent errors. Runtime exit codes are 0/1/2 for success/evaluation failure/usage or unreadable input.
- `runtime cli ARGS...` builds and forwards Rust CLI arguments; `quality lint ARGS...` forwards hyperlinter arguments. Wrapper `--help` is local; `-- --help` forwards help. Commands resolve files/imports from the repository root.

## Change Discipline

- Inspect declarations before naming or changing anything; never guess symbols.
- Reuse and extend existing helpers; avoid parallel implementations.
- Keep production code and tests in the same logical checkpoint, and add or tighten the nearest test when semantics change. Run tests through `./manage.py`; keep generated fixtures and artifacts inside the project, never `/tmp`.
- Stage explicit paths only (never `git add .` or `git add -A`) and commit each verified checkpoint with a detailed message.
- Update this file when architecture, invariants, or workflows materially change.

## Runtime safety

- CLI and public VM evaluation run on a fixed-size worker stack. Shared per-VM work/produced-data budgets, graph size and syntax/value/dependency/import depth guards must apply equally to run/check and computed functions; keep their boundary contracts in the `resources` suite.
- `Vm::load_sandboxed` and `runtime sandbox ROOT FILE` confine transitive file imports to caller-authorized canonical roots; empty roots permit embedded modules only. Program and recovery data never grant host permissions. This is path-based import confinement, not OS isolation; future effects require explicit capabilities.

- Trust verification: `semantics` maintains an actor/result/arity/type-failure census; `type_algebra` owns independent structural membership oracles; `memory` samples Linux peak RSS; `mutants` compiles isolated faults under `runtime/`. Keep witnesses current when adding actors, and preserve future suspension/effect/versioning obligations in Charter section 7.
