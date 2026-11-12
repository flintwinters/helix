# Helix

Helix is an experimental Rust runtime with YAML source, immutable execution values, and readable YAML results and diagnostics. Programs are object graphs; actor-first vectors such as `[add, x, 1]` are expressions. APIs, syntax and tooling should be discoverable by people and agents. The earlier C++ implementation lives in `flintwinters/helixcpp`.

## Motivation

Make execution as readable and explicit as source. Prioritize structured, agent-consumable outputs and hierarchical discovery. Retaining a machine must preserve its meaning while another version advances. Ordinary data round-trips through YAML; function and machine inspection graphs do not promise executable disk restoration.

The ultimate application is declarative machine configuration through first-class types: resolve a machine description purely before applying effects (`CHARTER.md`). The type/class algebra, pure regions, constraints and state comparison exist. Effectful machine application and the reversibility promise remain future work. Keep the build bootstrap independent of Helix execution.

## Architecture

- `src/model.rs` defines source graphs, dedicated runtime values and explicit execution state. Code, collections and machine versions share immutable storage with copy-on-write updates. Runtime functions and VMs are distinct from ordinary lists/maps.
- `src/graph.rs` preserves lexical topology, scalar quoting and source spans. Inspectable references are source-qualified paths with string-key and integer-index segments; arena indices and allocation addresses have no language meaning.
- `src/eval.rs` is the sole iterative request/continuation engine. Calls, actor and argument resolution, lazy forms, iteration progress, annotations, error unwinding, memoization, cycle guards, resource budgets and phase state are explicit. Functions retain original code and lexical environments; execution never emits or reparses function source.
- `src/vm.rs` supplies immutable stepping, running, inspection and independent-error checking. Nested machines advance through an iterative scheduler, without recursively running another interpreter. Terminal stepping preserves state. Checking evaluates the pure program; it is not static analysis of uncalled bodies.
- `src/builtins.rs` and `src/builtins/` own pure host operations and the type/class algebra. Refinements and whole-record constraints request predicates through the same engine; deterministic host traversals retain completed predicate answers. Host callbacks never execute Helix. A failing cleanup replaces the original outcome.
- Explicit `type_time` regions have phase-local memoization and shared work/data budgets. File dependencies are rejected on phase entry; caught errors retain phase information. Future effect requests must enforce phase and capability boundaries before acting.
- `src/link.rs` links and deduplicates literal imports before execution, preserving isolated module scopes and source identities. Confined loading authorizes canonical file roots supplied by the caller; source and recovery data cannot grant permissions. This is import confinement, not OS isolation.
- `src/value.rs`, `src/inspect.rs` and `src/emit.rs` own exact numeric comparison, cycle-aware structural function equality, stack-bounded retirement of retained execution graphs, source-aware object views and YAML/JSON boundaries. Ordinary data stays compact; function/machine results add named objects beside their value projection. Inspection does not execute the inspected machine.
- Pure failed-run recovery freezes completed ordinary-data fields and retries other fields from source. It does not restore active execution. Original source blame, navigable call chains, error causes and scalar styles remain public contracts.
- `std/` supplies pure testing, configuration vocabulary, reflection, collection and data-only YAML libraries. `std:test` owns assertions, generators, selection and report policy; Python owns process and filesystem boundaries. Parsing YAML data never reconstructs executable functions or machines.
- Tests combine golden source/error contracts, ordinary-data fixpoints, dogfooded laws, public execution APIs, retained-state and pause checks, capability/resource boundaries, mutation sensitivity, and time/memory observations. `manage.py` is the authoritative tooling entrypoint; command discovery lives in `tooling/cli.py`, and `tooling/test_runner.py` owns explicit per-run test contexts. Operational guidance belongs in `README.md`, `LANGUAGE.md` and `tests/README.md`.

## Current Big Tasks

1. Integrate and exercise immutable execution alongside ongoing language/tooling work; address the explicitly deferred structural-linter findings without weakening policies or baselining them away.
2. Design validated, versioned disk snapshots and reversibility-promise syntax, preserving complete explicit continuation state and readable execution references.
3. Introduce capability-controlled effect requests and declarative machine application, then build debugger interfaces on inspection. Effects and debugger UI are not implemented.
4. Preserve evaluated-value source locations beyond errors/inspection, and decide whether to support YAML aliases and non-scalar keys.
