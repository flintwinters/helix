# Helix

Helix is an experimental C++ runtime in which readable YAML is both program
source and persisted virtual-machine state. Programs are object graphs,
vectors are executable expressions, and completed or suspended execution can
be serialized back to YAML for inspection, editing, versioning, and resumption.

```text
C source -> HCC -> Helix YAML
                        |
YAML -> Cell graph -> evaluator -> builtins
                        ^             |
                        +-- VM state -+
```

## What works today

- YAML programs execute through a small object-graph runtime.
- VM source, frames, results, errors, and resumable state remain human-readable.
- Nested micro-VMs can be started, stepped, inspected, and debugged.
- Breakpoints and external Git-backed snapshots support branchable execution
  history.
- Parent-linked scopes provide lexical lookup, dotted object lookup, functions,
  typed fields, control forms, and structured errors.
- Native modules can expose platform facilities without coupling them to the
  core runtime.
- Stateless message handlers can process one request and return one response;
  the bundled HTTP demo exercises this boundary.
- A Python HUI prototype displays and edits literal YAML, overlays the active
  program counter, navigates semantic objects, and drives step, continue, and
  backward-history operations.

## HCC

HCC is the companion Python compiler for lowering a small C subset into
readable Helix YAML. It currently supports integer functions and parameters,
locals, calls, returns, assignment, arithmetic, compound assignment, unary
operators, `if`, `while`, integer casts, and ternary expressions.

The active compiler checkpoint is C source provenance: generated forms must
retain their original file, line, and column through serialization, runtime
frames and errors, and debugger presentation. Comparisons, complete integer
semantics, pointers, aggregates, globals, allocation, and libc remain later
work.

See [HCC_Plan.md](HCC_Plan.md) for the compiler roadmap and current design
questions.

## HUI

The current literal-YAML interaction model is implemented and deterministically
tested in Python. The next checkpoint is a portable C++ vertical slice with a
platform-neutral terminal byte-stream boundary, an in-memory test adapter, and
a POSIX adapter. Runtime evaluation and serialization remain owned by the
existing runtime rather than the UI.

See [HUI.md](HUI.md) for the interaction invariants and migration roadmap.

## Build and test

Helix requires Python 3.13+, `uv`, a C++20 `g++`, CMake, and a rapidyaml source
tree at `ryml/`. The root-level `manage.py` command is the canonical workflow
surface:

```bash
uv run python manage.py build              # build build/helix
uv run python manage.py cpp-test           # run runtime fixtures
uv run python manage.py cpp-test --optimize-size
uv run python manage.py hcc-test            # run compiler fixtures
uv run python manage.py hui-test            # run scripted HUI tests
uv run python manage.py webdemo-test        # verify HTTP handler turns
uv run python manage.py                     # full default project workflow
```

Run programs and compiler inputs directly with:

```bash
./build/helix path/to/program.yaml
uv run python -m hcc input.c -o out.yaml
uv run python manage.py hui-demo
uv run python manage.py webdemo
```

The production-size runtime profile uses `-Os`, LTO, disabled RTTI, stripped
output, and static size-optimized rapidyaml. It also disables dynamic module
loading and is therefore incompatible with `--lib`.

## Project direction

Helix exists to make execution state readable, persistent, and branchable.
Near-term work applies that model to C debugging and moves the proven terminal
interaction model into portable C++. Longer-term targets include RP2040 and an
FPGA RV32 softcore, with reproducible hardware inputs and outputs represented
as testable VM state.

## Demo

[![Helix demo video](https://img.youtube.com/vi/WamNcUSqt1w/hqdefault.jpg)](https://youtu.be/WamNcUSqt1w)
