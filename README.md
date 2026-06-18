# Helix

Helix is a native C++ system for building computation around structured, versionable object graphs. It is not a VM language, transpiler target, or managed runtime.

## Current Runtime

The current implementation interprets YAML object graphs as C++ cells. A program has a `main` entrypoint, symbolic string resolution, builtin forms, mutable VM state, includes, stepping, child VM execution, signals, and YAML fixture tests. These pieces are implemented today, but they are still early mechanisms for the larger microVM architecture.

## Direction

The core abstraction is the microVM: a small, isolated, message-passing computational object represented through a YAML-like graph. A microVM owns state, exposes addressable namespaces, can be inspected, versioned, paused, stepped, and may eventually run under deterministic, relative, or branching time.

Helix treats structured objects as primary. Files, devices, services, packages, debugger views, and low-level C++ resources are intended to appear through object interfaces; files are an implementation detail, not the model itself. YAML is the universal representation and interface layer, not necessarily the final storage format or performance path.

The architecture is influenced by Plan 9 namespaces, capability systems, actor models, version control systems, databases, deterministic simulators, and theorem provers, without depending directly on any one of them. Recurring design themes include state versioning, replay, branching histories, loose protocol-driven communication, capability declaration, knowledge-based package discovery, and optional formal verification as an analysis layer.

The long-term goal is to blur the boundaries between operating system, runtime, database, debugger, package manager, and version control system around programmable, versioned object state.
