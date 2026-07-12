# Helix Conceptual Map

## Purpose

Helix is an experimental C++ runtime in which readable YAML is both program source and persisted VM state. Programs are object graphs, vector forms are executable expressions, and completed or suspended execution is serialized back to YAML. HCC is the companion Python compiler that lowers a small C subset into readable Helix YAML.

## System Flow

```text
C source -> pycparser AST -> hcc/compiler.py -> Helix YAML
                                               |
YAML file -> ryml_interface -> Cell graph -> resolver/evaluator -> builtins
                                               |                    |
                                               +---- VM state <-----+
                                                        |
                                             YAML on stdout / TUI snapshots
```

The runtime starts at the root mapping's `main`. A vector such as `[add, x, 1]` resolves its first element as the actor and evaluates through the builtin zygote. The top-level mapping is a `VmCell`; nested mappings containing `main` also become `VmCell`s, while other mappings become `ScopeCell`s.

## Ownership Map

| Area | Responsibility |
| --- | --- |
| `src/core.hpp`, `src/core.cpp` | Cell hierarchy, parent links, lookup and object paths, structured errors, typed slots, VM state helpers |
| `src/helix.cpp` | Name resolution, form evaluation, frames, breakpoints, VM lifecycle, executable entrypoint |
| `src/builtins.cpp` | Zygote builtin installation, arithmetic/data/control forms, functions, child `start`/`step` |
| `src/ryml_interface.*` | YAML-to-Cell conversion, source locations, `include`, `name:type` sugar, YAML emission, native modules |
| `scripts/operations.py` | Canonical build, analysis, fixture discovery, runtime/HCC test harnesses |
| `run.py`, `hcc/build.py` | Thin repository and HCC workflow entrypoints; keep policy in `scripts/operations.py` |
| `hcc/compiler.py` | `pycparser` C AST lowering to human-readable Helix forms |
| `tests/`, `hcc/tests/` | Executable YAML specifications for runtime behavior and C lowering |
| `tui/helix_step.py` | Out-of-process stepping and branchable per-target Git snapshots |
| `lib/sfml/` | Optional `.so` module; it must remain isolated from the core runtime binary |

`ryml/`, `build/`, `debug_*`, caches, and local lock/environment files are ignored dependencies or generated state, not primary project source.

## Runtime Invariants

- Parent links define lexical/object resolution. Attach children through `MapCell::set()` and `VecCell::append()`; clear links deliberately when detaching trees.
- Persist resumable execution as root-relative object paths in `state.frames`, never as opaque pointers. VM statuses are `ready`, `running`, `finished`, `error`, or `signaled`.
- Signals are control flow. Propagate return/error signals immediately and materialize terminal state consistently under `state` (`status`, `frames`, `result`, and `error` when applicable).
- YAML shape is a public behavioral contract. Preserve source locations, structured error details, include semantics, and round-trippable state.
- Builtin argument vectors include the actor at index zero; use the shared arity/type/error helpers rather than open-coding validation.
- Dotted lookup, function definition scope, nested VMs, breakpoints, and typed assignments depend on parent topology. Clone only where ownership/isolation requires it.
- Typed field sugar (`x:i32: 5`) becomes `{type: i32, value: 5}`. Assignment validation currently supports `i32` and `i64`.

## Canonical Workflows

```bash
uv run python run.py build              # build build/helix
uv run python run.py cpp-test           # build and run tests/*.yaml
uv run python hcc/build.py              # build and run hcc/tests/*.yaml
uv run python run.py                     # build, tidy, runtime fixtures, LOC, size
./build/helix path/to/program.yaml       # execute one Helix program
uv run python -m hcc input.c -o out.yaml
uv run python tui/helix_step.py program.yaml
```

The project requires Python 3.13+, C++20 `g++`, and a locally built `ryml` library at `ryml/build`. Useful workflow flags include `--fail-fast`, `--valgrind`, `--no-dynamic-libraries`, `--no-cpp-linenums`, `--optimize-size`, and `--lib`.

Runtime fixtures contain exactly one of `program` or `source`; non-smoke fixtures provide `expected`, with optional stdout assertions. HCC fixtures provide `c_source` and normally assert both emitted `expected` YAML and `expected_state`. Add or tighten the nearest fixture whenever semantics change.

## Change Discipline

- Formulate behavior as explicit invariants and inspect declarations before naming or changing anything; never guess symbols.
- Reuse and extend existing helpers. Centralize repeated behavior and avoid expedient compatibility paths or parallel implementations.
- Keep production code and tests in the same logical checkpoint. Run the narrowest relevant suite, then the broader suite when shared runtime behavior changes.
- Do not stop merely because the tree is dirty. Preserve intent, stage explicit paths only—never `git add .` or `git add -A`—and commit every verified logical checkpoint with a detailed message.
- Update this map when architectural ownership, invariants, workflows, or project state materially changes.
- For UI work: use a dense high-contrast gruvbox dark presentation, minimal labels/spacing/borders, and no animations or transitions.

## Current Boundaries

- HCC intentionally targets readable output over optimization. The current lowerer handles integers, locals, functions/calls, returns, `if`, `while`, and arithmetic. Comparisons are planned; pointers, aggregates, allocation, and libc remain deferred.
- The TUI invokes the compiled runtime through temporary wrapper YAML and stores snapshots in a per-target debug repository; it is not an in-process debugger.
- Verification on 2026-07-12: HCC fixtures pass 3/3. Runtime fixtures pass 44/46; `demo_source.yaml` and `include.yaml` require the ignored root files `helix_demo.yaml` and `helix_demo_include.yaml`, which are currently absent.
