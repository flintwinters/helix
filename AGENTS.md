# Helix

## Motivation

Helix is an experimental runtime in which readable YAML is both program source and persisted machine state. Programs are object graphs; actor-first vectors such as `[add, x, 1]` are expressions. Keep source, results, and failures legible, diffable, and round-trippable, with discoverable APIs and tooling suited to agents.

The long-term niche is declarative machine configuration through first-class types: resolve and validate a configuration purely, then apply it in a separate effectful step. Full suspended execution and effectful machine application are not implemented.

Keep bootstrapping single-step: Helix must never be a prerequisite for building itself.

## Architecture

- `src/graph.rs` parses YAML into an arena with source spans and parent links; those links define lexical scope. `src/link.rs` links literal imports before evaluation and isolates module scope.
- `src/eval.rs` owns evaluation order, memoization, calls, closures, and resource accounting. `src/builtins.rs` registers actor identity, arity, and evaluation policy in shared descriptors and dispatches pure operations; `src/builtins/typing/` implements types, class composition, and machine-state comparison using the same evaluator for predicates.
- `src/vm.rs` exposes lifecycle, run/check, and recovery through the public API in `src/lib.rs`. Check collects evaluation failures, not static errors in uncalled code. Recovery preserves completed root fields and retries unfinished work; it does not restore active continuations.
- `src/value.rs` owns shared value inspection; `src/source.rs` owns persisted-source conversion, callable reloading, and recovery serialization. `src/emit.rs` preserves source versus literal data when emitting YAML. `src/error.rs` owns structured diagnostics and source blame.
- `std/` contains embedded Helix libraries. Build derived operations from existing primitives; pure testing policy belongs in `std:test`, while Python owns process and filesystem checks.
- `src/main.rs` owns language CLI behavior. `manage.py` is the single project-tooling entrypoint; `tooling/cli.py` owns its discoverable command tree. `hyperlinter/` is the upstream lint submodule.

## Current Big Tasks

[CHARTER.md](CHARTER.md) owns the detailed roadmap and acceptance criteria. Mark milestones complete only when they work end to end with a golden case.

1. Implement the reversibility promise and full suspend/resume, including live calls, locals, continuations, and pending effects.
2. Add capability-controlled effects and signals, leading toward machine application.
3. Preserve source locations on evaluated values.
4. Exercise Rust lint coverage as the runtime grows; contribute general function-cycle and dead-code support upstream.
5. Decide whether YAML anchors, aliases, and non-scalar keys remain deliberately unsupported.

## Invariants

- YAML shape is a public contract: preserve structured errors, source blame, and finished-root serialization fixpoints.
- A machine promising reversibility must expose all behavior and execution state needed for restoration. Hidden host state cannot determine its continuation. Persist execution references as root-relative paths, never opaque pointers.
- VM statuses are `ready`, `running`, `finished`, `error`, and `signaled`; not all are persistently restorable today.
- Type-time evaluation shares the ordinary evaluator and resource budgets, with isolated memoization. It currently excludes programs containing file imports. Future effects must enforce the purity boundary before acting.
- Resource guards must cover CLI and public VM execution, run/check, and computed functions equally. Import permissions come from the host, never program or recovery data; path confinement is not OS isolation. Future effects require explicit capabilities.

## Change Discipline

- Fix bugs without asking. Inspect declarations and search for reusable code before implementing; never guess symbols.
- Abstract shared concepts and invariants that must evolve together. Prefer limited duplication over coupling independently changing concepts.
- Keep changes verified and recoverable, APIs small, and testing infrastructure reusable. Follow the [development workflow](README.md#change-workflow) and [testing guide](tests/README.md).
- Keep this file concise and authoritative for motivation, architecture, invariants, and strategic work. Update it when those change; put API details, operational recipes, and test mechanics in their references below.

## Reference Map

- [LANGUAGE.md](LANGUAGE.md): syntax, builtin contracts, imports, purity, serialization, recovery, and runtime limits. Update it with language changes.
- [tests/README.md](tests/README.md): coverage, known gaps, test authoring, artifacts, and benchmarks.
- [README.md](README.md): setup, examples, and development entrypoints.
- `manage.py`: authoritative command and suite discovery; operational examples belong in the guides above.
