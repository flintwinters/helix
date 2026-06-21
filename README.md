# Helix

Helix is an experimental C++ runtime for structured, versionable object graphs.
Programs are represented as YAML graphs, executed by a native runtime, and tested
through fixture-based snapshots.

The repository also includes HCC, a small proof-of-concept compiler that lowers a
freestanding subset of C into readable Helix YAML.

## Current State

The runtime currently supports:

- YAML object graphs with a `main` entrypoint.
- Symbol resolution, dotted lookup, includes, builtin forms, and mutable VM state.
- Conditional execution, loops, function calls, child VM execution, stepping, and signals.
- YAML fixture tests for runtime behavior and expected errors.
- HCC fixture tests for C-to-Helix lowering and runtime execution.
- A debugger workflow that snapshots stepped YAML state into a local git history.

This is an early research codebase. The implementation is usable for the checked
fixtures and demos, but the object model and runtime interfaces are still changing.

## Requirements

- Python 3.13 or newer.
- `uv` for Python environment management.
- `g++` with C++20 support.
- A built `ryml` dependency at `ryml/build` for linking the C++ runtime.
- Optional: `clang-tidy-20`, `cloc`, `valgrind`, and SFML development libraries.

Install Python dependencies with:

```bash
uv sync
```

## Build and Test

Build the C++ runtime:

```bash
uv run python run.py build
```

Run the default runtime checks:

```bash
uv run python run.py
```

Run only native YAML fixtures:

```bash
uv run python run.py cpp-test
```

Run HCC compiler fixtures:

```bash
uv run python run.py hcc-test
```

Useful options:

- `--fail-fast` stops on the first failing fixture.
- `--valgrind` runs fixtures under Valgrind when available.
- `--no-dynamic-libraries` builds without dynamic library support.
- `--no-cpp-linenums` disables C++ source-location debug output.
- `--optimize-size` adds size optimization flags.
- `--lib` also builds the optional SFML native module.

## Running Programs

After building, run a Helix YAML program with:

```bash
./build/helix helix_demo.yaml
```

Compile a supported C source file to Helix YAML with:

```bash
uv run python -m hcc path/to/source.c -o program.yaml
```

Omit `-o` to write generated YAML to stdout. Pass `--cpp` when the C source
needs preprocessing before parsing.

## Debugger

`tui/helix_step.py` advances a target YAML VM by one child-VM step. By default it
copies the target into a local `debug_<name>` directory, initializes a git
repository there, and commits each stepped state as a snapshot. Runtime state
stored in YAML, including `state.status`, `state.frames`, `state.result`, and
object-path program counters, becomes ordinary versioned program data.

Step a program with:

```bash
uv run python tui/helix_step.py program.yaml
```

Useful options:

- `--tui` enables interactive branch and snapshot navigation.
- `--no-vcs` runs without git-backed state history.
- `--binary /path/to/helix` uses a specific runtime binary.

## Repository Hygiene

The public source set is intentionally small: runtime sources, Python tooling,
fixtures, demos, and documentation. Local build products, Python caches,
debugger snapshots, compile databases, virtual environments, and transient
program-state files are ignored.

## Direction

The core abstraction is the microVM: a small, isolated, message-passing
computational object represented through a YAML-like graph. A microVM owns state,
exposes addressable namespaces, can be inspected, versioned, paused, stepped, and
may eventually run under deterministic, relative, or branching time.

Helix treats structured objects as primary. Files, devices, services, packages,
debugger views, and low-level C++ resources are intended to appear through object
interfaces; files are an implementation detail, not the model itself. YAML is the
current representation and interface layer, not necessarily the final storage
format or performance path.

The architecture is influenced by Plan 9 namespaces, capability systems, actor
models, version control systems, databases, deterministic simulators, and theorem
provers, without depending directly on any one of them. Recurring design themes
include deterministic replay, richer branching histories, protocol-driven
communication, capability declaration, package discovery, and optional formal
verification as an analysis layer.

The long-term goal is to narrow the gap between operating system, runtime,
database, debugger, package manager, and version control system around
programmable, versioned object state.
