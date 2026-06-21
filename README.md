# Helix

Helix is an experimental C++ runtime for structured, versioned program state.

## Overview

- YAML programs run in a native C++ runtime, so program state can be stored,
  inspected, and edited as ordinary structured data.
- The runtime can step programs, call functions, run loops and branches, resolve
  names, include files, enforce typed slots, and report structured errors.
- Programs can start and step child VMs, which makes nested execution explicit
  instead of hiding it inside a call stack.
- The debugger saves each stepped YAML state as a git commit, so execution can
  be rewound, branched, compared, and resumed with normal version-control tools.
- HCC compiles a small C subset into readable Helix YAML, making compiled output
  inspectable instead of opaque.
- Runtime and compiler behavior are covered by YAML fixtures, so current behavior
  is easy to review and regression-test.

## Working Today

Programs are represented as YAML graphs and executed by a native C++ runtime. A
program has a `main` entrypoint, mutable VM state, symbolic resolution, dotted
lookup, includes, builtin forms, typed slots, conditionals, loops, functions,
child VM execution, stepping, and signals.

The debugger already versions execution state. `tui/helix_step.py` copies a
target VM into `debug_<name>`, initializes git there, and commits each stepped
YAML state. Runtime fields such as `state.status`, `state.frames`,
`state.result`, and object-path program counters are ordinary versioned data, so
execution can be inspected, rewound, branched, and resumed.

HCC is included as a proof-of-concept compiler for lowering a small freestanding
C subset into readable Helix YAML. Its emitted programs are checked both as YAML
output and as executable input to the same C++ runtime.

This is an early research codebase. The implementation is usable for the checked
fixtures and demos, but the object model and runtime interfaces are still changing.

## Tooling

- Python 3.13 or newer.
- `uv` for Python environment management.
- `g++` with C++20 support.
- A built `ryml` dependency at `ryml/build` for linking the C++ runtime.
- Optional: `clang-tidy-20`, `cloc`, `valgrind`, and SFML development libraries.

Common commands:

```bash
uv sync
uv run python run.py build
uv run python run.py
uv run python run.py cpp-test
uv run python run.py hcc-test
./build/helix helix_demo.yaml
uv run python -m hcc path/to/source.c -o program.yaml
uv run python tui/helix_step.py program.yaml
```

`run.py` supports `build`, `cpp-test`, and `hcc-test`; with no subcommand it runs
the default build, tidy, fixture, line-count, and binary-size checks. Shared
options include `--fail-fast`, `--valgrind`, `--no-dynamic-libraries`,
`--no-cpp-linenums`, `--optimize-size`, and `--lib`.

HCC is available as `uv run python -m hcc`. It writes YAML to stdout by default
or to `-o`; `--cpp` enables preprocessing before parsing.

## Debugger

`tui/helix_step.py` advances a target YAML VM by one child-VM step. The default
workflow copies the target into `debug_<name>`, initializes git there, and
commits each stepped state. `--tui` enables branch and snapshot navigation,
`--no-vcs` disables the snapshot repository, and `--binary` selects a runtime
binary.

## Repository Hygiene

The public source set is intentionally small: runtime sources, Python tooling,
fixtures, demos, and documentation. Local build products, Python caches,
debugger snapshots, compile databases, virtual environments, and transient
program-state files are ignored.

## Planned Direction

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
