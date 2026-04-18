# Helix

## Intro:
Helix is a small experimental runtime written in C++. It loads YAML input,
builds an internal cell graph, and evaluates that graph through a compact
runtime model centered around maps, vectors, strings, numbers, and builtin functions.
Text rendering is centralized in `Cell::str` rather than being redefined on each concrete cell subclass.

### Detail:
Helix is a minimal, object-uniform systems language implemented in C++, with semantics inspired by Smalltalk. Every value is a cell, concretely represented as a map, and behavior emerges from the presence or absence of protocol slots rather than fixed types. There are only two fundamental operations: find and eval. Find performs name lookup with optional delegation through a parent link, while eval provides invocation semantics for any cell that defines it. Environments, closures, native functions, and even execution contexts are not distinct runtime categories but compositions of these capabilities. Errors are first-class cells, ensuring failure propagates uniformly without out-of-band control flow. The result is a highly regular system where objects can dynamically assume roles such as namespace, function, or evaluator without changing representation.

Any cell can operate as a self-contained mini VM by implementing a `state` field that contains execution data such as stack, frames, and environment. Execution is therefore not centralized; instead, control flows by passing through cells that locally host and evolve their own state, effectively acting as composable, nested interpreters.

To eliminate parser complexity and accelerate experimentation, the language uses YAML directly as its surface syntax. Programs are authored as YAML documents that map naturally onto the underlying cell structure, making code both data and executable form simultaneously. This approach enables rapid iteration on semantics without committing to a custom grammar, while preserving readability and structural clarity. Since YAML already encodes maps, sequences, and scalars, it aligns closely with the runtime model, allowing the interpreter to operate directly on parsed data structures. The combination of a Smalltalk-like message-passing core, C++-level control over memory and performance, and YAML-based syntax yields a compact but expressive experimental platform for declarative and reflective systems programming.

## Exec:
Python is used as a buildscript. `python3 build.py` compiles the `helix` binary and executes the test suite with valgrind.  Helix uses arena allocation for memory safety.







### Planned:

- Inline c++
- Inline assembly
- sfml.cpp wrappings

```
├── helix
├── README.md
├── build.py
└── src
    └── builtins.cpp
```
