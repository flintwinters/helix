# Helix

Helix is an experimental Rust runtime: readable YAML is program source, ordinary data round-trips through YAML, and immutable machine values expose execution for inspection and branching. Syntax, output, APIs and tooling should be discoverable by agents and people. The previous C++ implementation lives in `flintwinters/helixcpp`. Fix bugs without asking.

## Motivation

Keep programs and execution state readable, inspectable and explicit. A retained machine must preserve its meaning when another version advances. Disk restoration remains a separate design problem: inspection output is not an executable snapshot format.

The ultimate niche is declarative machine configuration through first-class types (`CHARTER.md` section 8): resolve a machine type purely before applying effects. The type/class algebra and state comparison exist; effectful application does not.

## Architecture

Rust crate `helix` (library and unified CLI, including `helix exec` pipelines); `./manage.py` is the single tooling entrypoint. Keep builds independent of Helix execution.

- `src/model.rs` holds shared syntax, runtime-value and execution-state definitions. Source graphs are immutable after load-time linking. Runtime values distinguish functions and machines from ordinary data; lists/maps, code and machines share immutable allocations with copy-on-write updates.
- `src/graph.rs` parses YAML events into source nodes with lexical parent links, scalar quoting and source spans. Arena indices are private accelerators. Inspectable references carry a source identity and typed root-relative path segments: string keys and integer indices. Errors retain a human-readable `path` and an unambiguous typed `reference`.
- `src/eval.rs` is the sole execution dispatcher. Owned requests represent evaluation, argument/actor resolution, calls, annotations, lazy forms, iterations, returns and error unwinding. Environments, memoized results, cycle guards, intermediate outcomes and limits belong to the machine. Functions retain direct code and lexical-environment references; execution never emits/reparses function source. Recursive lexical-binding equality uses a visited-pair traversal.
- `src/vm.rs` supplies pure `Vm::step(&self) -> Vm`, terminal-state stability and `run(&mut self)` over the same transitions. Its iterative scheduler descends into child advancement with an explicit parent vector, without recursive interpreter calls. `[vm, function]` creates a ready zero-argument child; `[step, child]` advances a version; `[inspect, child]` exposes read-only data without running it. `check` collects independent errors using the same engine; it does not statically check uncalled bodies.
- `src/builtins.rs` and `src/builtins/` own pure host operations, arities, value inspection, type/class algebra and machine-state comparison. Type operations journal completed predicate answers and replay their deterministic host traversal when requesting another predicate. The dispatcher executes each requested predicate; host callbacks never execute Helix. A failing `finally` cleanup replaces the original outcome. Keep `LANGUAGE.md` synchronized with semantics.
- `[type_time, expression]` uses explicit phase-entry/return continuations and a phase-local memo table. Work/produced-value budgets remain shared; file dependencies are rejected on phase entry. Errors retain `phase: type`, including when caught. Pure class constraints and reflection use the same pending-predicate machinery.
- `src/link.rs` links literal imports once at load time, deduplicating canonical file/module identities and detecting cycles. Modules have isolated lexical scope. `src/library.rs` embeds `std/`; discover modules with `./manage.py inspect std`.
- `src/value.rs`, `src/inspect.rs` and `src/emit.rs` own equality, finite named object views and YAML boundary rendering. Mixed integer/float equality and ordering are exact. Function/machine-bearing results keep their ordinary `root` or `error` projection and add an `objects` table to the state envelope; these views are not supported restoration input. User data cannot collide with envelope metadata. Ordinary data stays compact and round-trippable.
- Pure failed-run recovery (`recover`/`resume`) freezes completed ordinary-data root fields and retries other fields from original source, including runtime objects. It does not restore active execution. Source recovery preserves scalar styles without encoding executable strings as YAML floats. Errors preserve source blame, innermost-first call sites and map re-raise causes.
- `std/yaml.yaml` provides data-only conversion/path operations through the shared bounded graph parser and emitter. Runtime functions/machines cannot be reconstructed by data parsing. `std:machine`, `std:map`, `std:set`, `std:stack` and `std:queue` supply pure configuration/data tools.
- `std/test.yaml` owns pure assertions, cases, tables, bounded properties, sample generation, selection/reruns and report aggregation. `tests/dogfood/**/*.yaml` exports reports; Python owns process/filesystem boundaries. Prefer dogfood laws for pure library behavior and host contracts for process behavior.

## Validation and tooling

- `./manage.py` shows `build`, `runtime`, `inspect` and `quality`; flat shortcuts remain equivalent. Append `--help` at any level. `--help-json` emits the versioned command/argument/suite tree without builds or dependencies. `tooling/cli.py` is authoritative for discovery.
- Run all tests through `./manage.py test`; use `--list`, `--describe`, or repeated `--suite NAME`. Golden cases live in `tests/cases/` and `tests/check/`. `--bless` deliberately updates expectations. Ordinary-data roots are checked as emission fixpoints; function/machine output is inspected rather than treated as executable source.
- `execution` exercises stepping, pauses, retained-state immutability, child machines, recursion, iteration, refinements and unwinding through Rust integration tests. Allocation-accounted workloads record execution time and live/peak bytes in `runtime/execution-profile.log`. `library` owns basic public-VM contracts. The harness, CLI, generated, imports, parser, dogfood, fuzz, scale and robustness suites cover the remaining boundaries.
- Golden/robustness cases use bounded parallel workers (`--jobs N`); suite discovery stays build-free and independent of execution-only imports. Trust suites cover actor witnesses, type algebra, source/parser contracts, confined imports, work/data/depth limits, RSS scaling and compiled mutations. `speed` retains compatible timing baselines under `runtime/speed/`.
- Interpreter subprocesses have a 10-second timeout (`HELIX_TEST_TIMEOUT` override). Generated/replay/minimized inputs and profiling artifacts belong in `runtime/`, never `/tmp`. Execution-suite timeouts kill the complete Cargo/test process group.
- `hyperlinter/` is the pinned `flintwinters/hyperlinter` submodule. `./manage.py lint [--format=text]` runs it from this repository root; private Git metadata holds baselines and its append-only ledger. Never weaken policy or baseline away findings. Structural-lint cleanup for the execution migration is explicitly deferred by the user; do not let it block behavioral integration.

## Current Big Tasks

1. Integrate and exercise the immutable execution engine; then address the deferred structural-linter findings without changing its policies.
2. Design validated, versioned disk snapshots and the reversibility promise type. All behavior and continuation state must remain explicit; inspection output alone is not a restoration contract.
3. Add effect requests and declarative machine application through an explicit outcome boundary, then build debugger interfaces over inspection. External effects, adapters and debugger UI are not implemented.
4. Preserve source locations for evaluated values, beyond errors and execution inspection; decide the fate of YAML anchors/aliases and non-scalar keys (currently rejected).

## Runtime safety

- Keep graph/syntax/import/dependency depth, work and produced-data limits in run, step and check. Immutable machines retain their remaining budgets; type-time regions do not reset them. Iterative execution replaces the old recursive worker-stack requirement.
- `Vm::load_sandboxed` and `runtime sandbox ROOT FILE` restrict transitive file imports to caller-supplied canonical roots. Empty roots allow embedded modules only. Recovery/program data never grant host permissions. This is path-based import confinement, not OS isolation.
- The source main checkout may contain independent uncommitted work. Integrate committed changes on this branch without overwriting that checkout.

## Invariants and change discipline

- Immutable versions never share mutable execution state. Host pointers and allocation identities have no language meaning. Persist execution references as source-qualified typed root-relative paths, never pointers or arena indices.
- Statuses are `ready`, `running`, `finished`, `error`, and `signaled`. Terminal stepping is unchanged; the same dispatcher drives run, step, callbacks and nested machines.
- YAML shape, structured blame, deterministic output and ordinary-data round trips are public contracts. Object-graph inspection does not promise disk compatibility or executable function reconstruction.
- Inspect declarations and search for reusable code before implementing. Keep authoritative concepts and invariants DRY; avoid abstractions coupling independently changing concepts.
- Keep production changes and relevant tests in each verified checkpoint. Stage explicit paths only; never `git add .` or `git add -A`. Commit each verified checkpoint with a detailed message.
- Keep this file concise and current when architecture, invariants or workflows change. Track broad work here; re-infer small tasks from source.
