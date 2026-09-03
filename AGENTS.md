# Helix Conceptual Map

## Motivation

Helix makes execution state readable, persistent, and branchable. YAML is both
program source and serialized VM state: programs are object graphs, vectors are
executable forms, and suspended or completed execution can be inspected,
edited, versioned, and resumed. HCC exists to bring that debugging model to C;
readable generated YAML serves debugging fidelity rather than being an end in
itself.

Prefer designs that keep state and control flow explicit. Preserve human-readable
representation and deterministic behavior across execution, serialization, and
debugger boundaries. Platform integrations should expose narrow capabilities
without hiding program state or contaminating the portable core.

## Architecture

```mermaid
flowchart LR
    C[C source] --> HCC --> YAML[Helix YAML] --> Runtime --> State[Serialized state]
```

The root mapping and nested mappings containing `main` are VMs; other mappings
are scopes. Evaluation begins at the root `main`. Parent links define lexical
and object topology, while resumable frames identify executable objects by
root-relative paths.

| Area | Authority |
| --- | --- |
| `src/core.*` | Cell model, ownership topology, lookup paths, errors, typed slots, VM state |
| `src/helix.cpp` | Resolution, evaluation, frames, breakpoints, VM lifecycle, executable |
| `src/builtins.cpp` | Builtin zygote, data/control forms, functions, nested-VM operations |
| `src/ryml_interface.*` | YAML conversion/emission, locations, includes, field sugar, native modules |
| `hcc/compiler.py` | C AST lowering to readable Helix forms |
| `tui/` | Host-side debugger and literal-YAML interaction prototype |
| `webdemo/`, `lib/` | Optional native integrations isolated from the core runtime |
| `manage.py`, `scripts/operations.py` | Canonical command surface and workflow policy |
| `tests/`, `hcc/tests/`, `tui/tests/` | Executable behavioral specifications |

`HCC_Plan.md` and `HUI.md` are the authoritative design cockpits for their
subsystems. `ryml/`, `build/`, debug repositories, caches, and local environment
files are dependencies or generated state, not primary source.

## Durable Invariants

- Attach and detach cells through the shared container APIs; parent topology is
  semantic and controls lookup, functions, nested VMs, breakpoints, and types.
- Persist frames as root-relative object paths, never opaque process pointers.
- Treat signals as control flow and materialize terminal VM state consistently.
- Treat YAML shape as a public contract. Preserve locations, structured errors,
  include behavior, and round-trippable state.
- Reuse shared builtin arity, type, and error helpers. Builtin argument vectors
  include the actor at index zero.
- Clone only when ownership or isolation requires it.
- Native communication is stateless per turn: invoke a Helix handler with one
  message and return one response without module-owned handler state.
- HUI styling, selection, and PC presentation are overlays, never persisted
  source. Navigation follows semantic object paths, and history remains external
  versioned YAML rather than evaluator state.

## Canonical Workflows

```bash
uv run python manage.py build
uv run python manage.py cpp-test
uv run python manage.py cpp-test --optimize-size
uv run python manage.py hcc-test
uv run python manage.py hui-test
uv run python manage.py webdemo-test
uv run python manage.py                 # full default workflow
./build/helix path/to/program.yaml
uv run python -m hcc input.c -o out.yaml
uv run python manage.py hui-demo
uv run python manage.py webdemo
```

The project requires Python 3.13+, `uv`, C++20 `g++`, CMake, and `ryml/`.
`--optimize-size` is the stripped, LTO, no-RTTI production profile and excludes
dynamic module loading; it is incompatible with `--lib`.

Tests belong in the established fixture suites and run through `manage.py`.
Runtime fixtures contain exactly one of `program` or `source`; raw programs for
source-backed fixtures live under `tests/assets/`. HCC fixtures assert emitted
YAML and final state. Do not create ad-hoc test scripts or use `/tmp` for tests.

## Current Jobs

- HCC: carry C file, line, and column provenance through generated YAML,
  serialization, frames, runtime errors, and debugger presentation. Do this
  before expanding the supported C subset. See `HCC_Plan.md`.
- HUI: replace the proven Python interaction prototype with the smallest
  portable C++ vertical slice. Keep frontend logic behind a byte-stream boundary,
  test it with an in-memory adapter, and implement POSIX without moving terminal
  concerns into the evaluator. See `HUI.md`.
- Embedded direction: keep the size-optimized runtime viable for RP2040 and an
  FPGA RV32 softcore; defer broad platform work until the C debugging and C++ HUI
  boundaries are sound.

## Change Discipline

- Search declarations and existing implementations before designing or naming
  anything. Reuse shared modules and helpers.
- Keep each invariant authoritative in one place. Abstract concepts that must
  evolve together; tolerate limited duplication when concepts vary independently.
- Prefer the smallest elegant design that preserves the architecture. Avoid
  compatibility paths and parallel implementations without a durable need.
- Add or tighten the nearest routinized fixture with every semantic change. Run
  the narrow suite first and broader suites when shared contracts change.
- Preserve unrelated work in dirty trees. Stage explicit paths only; never use
  `git add .` or `git add -A`.
- Commit every verified logical checkpoint with a detailed message.
- Keep this file concise and architectural. Update it only when motivation,
  ownership, durable invariants, workflows, or current high-level jobs change.
- UI work uses dense high-contrast gruvbox dark styling, minimal chrome, and no
  animations or transitions.
