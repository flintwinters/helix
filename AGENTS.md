# Helix

Helix is an experimental runtime in which readable YAML is both program source and persisted VM state. Programs are object graphs, vector forms such as `[add, x, 1]` are executable expressions (actor first), and completed or suspended execution serializes back to YAML.
Outputs and syntax should be agent-first and agent-friendly, and all APIs and tooling should be discoverable.

The previous C++ implementation lives in `flintwinters/helixcpp`.

Fix bugs without asking.

## Motivation

Program and machine state share one human-readable representation, so a suspended run can be read, diffed, edited, and resumed as plainly as source. Every design choice should keep that round trip lossless and legible.

The ultimate niche is declarative machine configuration (where Nix and Ansible sit), reached through declarative, first-class types: a machine config is a type resolved entirely at type time, inheriting from abstract machine type classes, and applying it is a separate effectful step (`CHARTER.md` section 8). The pure type/class algebra and state comparison exist; effectful machine application does not.

## Architecture

Rust crate `helix` (lib + `helix run <file|->` CLI); `./manage.py` is the single entrypoint (`build`, `run`, `test [--bless]`).

- `src/graph.rs`: YAML (via `yaml-rust2` events) into an arena of nodes with parent links and 1-indexed line/column spans; root-relative paths (`/inner/y/1`); lexical `lookup` walks parent links through enclosing maps.
- `src/vm.rs`: `Vm` with the five statuses and `state()` serialization (`status` plus `root` or `error`), around an `Evaluator`.
- `src/eval.rs`: `Evaluator` owns the graph, memoized evaluation with cycle detection, lexical symbol resolution (`resolve`: enclosing maps, `fn` parameters read from the call `Frame` stack, `let` bindings), and the forms with their own evaluation order (`builtins::is_form`: `if`/`cond`/`and`/`or`/`let`/`fn`/`try`/`finally` and the iteration forms). A sequence is an expression iff its head is a plain scalar naming a builtin or resolving to a user `[fn, [params], body]`; plain-string arguments are symbols, quoted strings are literals. Nodes inside a `fn` are never memoized and are cycle-checked per call depth; recursion is bounded by `MAX_DEPTH`. A `fn` declaration retains raw source (`Evaluator::raw`, emitted verbatim via `Yaml::Real`); when passed or returned, free lexical bindings serialize in a `closure` binding map. Computed functions work as call heads, through `call`, and in callback forms.
- `src/builtins.rs` (+ `builtins/{number,collection,fault,value}.rs`): pure builtins over evaluated values, with the arity table. `fault` and `value` are the shared leaf modules (children must not import their parent: hyperlinter treats that as a cycle). Failures are `Fault`s the VM maps onto the blamed argument node. The user-facing semantics live in `LANGUAGE.md`; keep it in sync when builtins change.
- `src/builtins/typing/`: type parsing, membership/subtyping, constructors/reflection, class inheritance, and pure machine-state comparison. Types are primitive names or ordinary declaration maps; refinements use the evaluator’s function machinery.
- `src/link.rs` links literal imports at load time into detached graph subtrees, deduplicated by canonical file path or standard module name, with cycle detection. Module scope is isolated; dotted symbols traverse map/module members. `src/library.rs` embeds `std/` Helix sources; `./manage.py std [module]` discovers them. Imported errors carry `file` with file-relative paths and spans.
- `std/math.yaml` and `std/list.yaml` exercise function parameter/result annotations in library code. Numeric contracts share `std:math.number`; library contract regressions cover imports and emitted-state round trips.
- `src/emit.rs`: Helix's own block-YAML emitter. It exists so output stays re-readable: it quotes strings that would re-read as another type or, as the first item of a list, as an expression head.
- `src/error.rs`: `HelixError` with stable `kind`, message, root-relative `path`, and `line`/`column`; serialized under `error:`.
- Tests: golden cases in `tests/cases/NAME.yaml` + `NAME.expected.yaml`; finished states are also checked to round-trip (emitted `root` re-run is a fixpoint).
  A robustness sweep re-runs damaged variants of every case (line prefixes, line deletions, char prefixes) and requires a well-formed state: exit 0/1, valid YAML, errors with `kind`/`message`/`line`/`column`.

- Lint: `hyperlinter/` is a submodule (`flintwinters/hyperlinter`); `./manage.py lint [--format=text]` runs it from the repo root (it loads Cargo.toml and `.git/hyperlinter/` baselines there). Its ledger lands in git-ignored `runtime/`. Keep the tree at zero diagnostics; do not weaken policy or write a baseline to silence findings. The Rust adapter models each `mod` file as its own module and leaves encapsulation to rustc (entrypoint rules HL108/HL109 do not apply; HL117 bounds `mod` fan-out). Function cycles, dead implementation, and clone rules are still TypeScript-only. Rust style here: private `mod`s with a small `pub use` surface in `src/lib.rs` where it reads well, otherwise ordinary `pub mod` paths; prefer `pub(crate)`/private over `pub` for items no other crate needs.

## Current Big Tasks

The human-facing roadmap and daily-driver checklist is `CHARTER.md`; tick items there only when they work end to end with a golden case. The list below is the immediate agent queue.

1. Suspend/resume through a reversibility promise type: a machine mixing in that promise must expose all behavior and complete execution state for serialization, including stack, heap, locals, continuations and pending effects as needed. Execution references remain root-relative paths. The promise and suspend/resume are not implemented; today the VM runs to completion or error in one `run`.
2. Grow the language past the pure core (done: values, operators, control flow, functions, iteration): side effects/signals (done: file/standard imports, `try`/errors-as-data, `assert`). Functions and declared types are first-class values.
3. Preserve source locations in the output for evaluated values, not only for errors.
4. Exercise hyperlinter as the Rust code grows and feed general (non-Helix-specific) gaps back upstream: Rust function-cycle/dead-code coverage, and the `runtime/` ledger location (README says state lives under `.git/hyperlinter/`).
5. Decide the fate of YAML anchors/aliases and non-scalar keys (currently rejected as `unsupported`).

## Invariants

- YAML shape is a public behavioral contract: preserve source locations, structured error details, and round-trippable state.
- Reversibility is a promise type: a machine mixing it in must expose all behavior and complete state so serialization can always capture and restore its execution. Hidden host state cannot determine its continuation.
- Persist resumable execution references as root-relative object paths, never opaque pointers.
- VM statuses are `ready`, `running`, `finished`, `error`, or `signaled`.
- Parent links define lexical/object resolution.

## Change Discipline

- Inspect declarations before naming or changing anything; never guess symbols.
- Reuse and extend existing helpers; avoid parallel implementations.
- Keep production code and tests in the same logical checkpoint, and add or tighten the nearest test when semantics change.
- Stage explicit paths only (never `git add .` or `git add -A`) and commit each verified checkpoint with a detailed message.
- Update this file when architecture, invariants, or workflows materially change.
