# Helix

Helix is an experimental runtime in which readable YAML is both program source and persisted VM state. Programs are object graphs, vector forms such as `[add, x, 1]` are executable expressions (actor first), and completed or suspended execution serializes back to YAML.

The previous C++ implementation lives in `flintwinters/helixcpp`.

## Motivation

Program and machine state share one human-readable representation, so a suspended run can be read, diffed, edited, and resumed as plainly as source. Every design choice should keep that round trip lossless and legible.

## Architecture

Rust crate `helix` (lib + `helix run <file|->` CLI); `./manage.py` is the single entrypoint (`build`, `run`, `test [--bless]`).

- `src/graph.rs`: YAML (via `yaml-rust2` events) into an arena of nodes with parent links and 1-indexed line/column spans; root-relative paths (`/inner/y/1`); lexical `lookup` walks parent links through enclosing maps.
- `src/vm.rs`: `Vm` with the five statuses, memoized evaluation with cycle detection, and `state()` serialization (`status` plus `root` or `error`). A sequence is an expression iff its head is a plain scalar naming a builtin (`add`, `sub`, `mul`, exactly 2 integer args); plain-string arguments are symbols, quoted strings are literals.
- `src/error.rs`: `HelixError` with stable `kind`, message, root-relative `path`, and `line`/`column`; serialized under `error:`.
- Tests: golden cases in `tests/cases/NAME.yaml` + `NAME.expected.yaml`; finished states are also checked to round-trip (emitted `root` re-run is a fixpoint).

## Current Big Tasks

1. Suspend/resume: persist execution position as root-relative paths and resume from serialized state (today the VM runs to completion or error in one `run`; `ready`/`running`/`signaled` are never persisted).
2. Grow the language beyond integer arithmetic: user-defined actors, control flow, side effects/signals.
3. Preserve source locations in the output for evaluated values, not only for errors.
4. Decide the fate of YAML anchors/aliases and non-scalar keys (currently rejected as `unsupported`).

## Invariants

- YAML shape is a public behavioral contract: preserve source locations, structured error details, and round-trippable state.
- Persist resumable execution as root-relative object paths, never opaque pointers.
- VM statuses are `ready`, `running`, `finished`, `error`, or `signaled`.
- Parent links define lexical/object resolution.

## Change Discipline

- Inspect declarations before naming or changing anything; never guess symbols.
- Reuse and extend existing helpers; avoid parallel implementations.
- Keep production code and tests in the same logical checkpoint, and add or tighten the nearest test when semantics change.
- Stage explicit paths only (never `git add .` or `git add -A`) and commit each verified checkpoint with a detailed message.
- Update this file when architecture, invariants, or workflows materially change.
