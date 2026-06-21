# Helix

Helix is an experimental C++ runtime for structured, versioned program state.

## Current Features Overview

IMPLEMENTED NOW:
- Running programs can be stored, inspected, and edited as ordinary human readable YAML.
- Helix Micro Virtual Machines can run Helix code repeatably and disposably.
- MicroVM running *state* and source code is fully human readable YAML.
- Version a running program/VM - branch, checkout, and merge program state.
- Breakpoints can trigger commits.
- Programmatically start, step, and debug nested child VMs.

## In progress

- Compile C99 into readable Helix YAML, to use Helix's debuggability in C

## Next

- Run on RP2040
- Run on FPGA RV32 softcore (icesugar pro, vexriscv, litex)
- Lean4 library to operate on Helix YAML source as a hyperlinter

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
