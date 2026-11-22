# Helix

Think like a mathematician would.

## Motivation

Make execution as readable and explicit as source. Helix is an experimental Rust
runtime whose YAML representation graphs describe immutable execution values and
machines. Retaining a machine must preserve its meaning while another advances.
Ordinary data round-trips through YAML; inspection is not executable restoration.
External source/state edits do not invalidate completed work or certify edited values.

The defining application is declarative machine configuration: resolve and validate
purely, then apply explicitly authorized effects. Keep build bootstrap independent of
Helix execution. Expose APIs and CLI tooling; building a UI is out of scope.
Complexity must earn its place through concrete workloads, under
[CHARTER.md](CHARTER.md), especially its admission and consolidation rules.

Dogfood substantive development work both to accomplish it and to improve Helix
through real language, library and runtime feedback. Useful dogfood need not be
shorter than host code. Compose ordinary reusable functions for a workload instead
of exposing its fixed harness workflow as a public API. Keep one implementation of
each responsibility; independent observations and fault injection detect failures
without duplicating the same decisions in another language.

## Architecture and authoritative entrypoints

- [LANGUAGE.md](LANGUAGE.md) owns syntax and execution semantics. Write source only with
  `!call`/`!ref`/`!literal` tags; `{$call: ...}` maps are their normalized representation
  (JSON, error paths, inspection), and the static suite rejects them as written YAML.
  [src/model.rs](src/model.rs), [src/graph.rs](src/graph.rs) and
  [src/eval.rs](src/eval.rs) lead into the immutable model and iterative engine.
- [src/vm.rs](src/vm.rs) owns public machine execution;
  [src/snapshot.rs](src/snapshot.rs) owns versioned restoration.
  Snapshot closure and portability boundaries belong to charter section 2.
- [HOST.md](HOST.md) owns capabilities, effects, command/SSH lifecycle and durable
  recovery. Authority comes from callers, never source or saved state. Journals refuse
  uncertain replay; process cleanup is not remote fencing, rollback or exactly-once execution.
- [LIBRARY.md](LIBRARY.md) owns embedded library contracts. Declarative application
  composes types, inventory, orchestration and providers through the existing engine.
  Fixture/operator contracts under [tests/README.md](tests/README.md) define qualified
  environments; passing a scoped workload does not qualify general fleets or services.
- [manage.py](manage.py) is the single tooling/testing entrypoint;
  [tooling/cli.py](tooling/cli.py) owns discovery and
  [tooling/test_runner.py](tooling/test_runner.py) owns explicit test contexts.
  [tests/README.md](tests/README.md) owns testing operations and evidence.
  Use the CLI wrappers so runtime invocations reach the SQLite usage log.
  Golden checks compose general `std:test` assertions and reports in Helix.
  Python captures and validates observations, supervises ordinary-root replay and
  writes explicitly blessed expectations after checks pass; it does not duplicate
  Helix comparisons. Goldens cover observable serialization contracts; semantic
  tests assert focused values. Harness fault injection independently checks rejection.
- [src/tooling/](src/tooling/) leads into analysis, reflection, search and editor tooling.
  Reuse authoritative syntax and lexical selection. Static analysis never executes;
  unknown evidence stays explicit, and evaluated-value provenance stays bounded.

Follow these entrypoints into source instead of duplicating inventories or operational
instructions here. Keep the [README](README.md) concise. Prefer scoped, actionable
agent-consumable output with progressive discovery and explicit complete JSON reports.

Agents are the primary users: prefer the design a model gets right first try, judged by recorded agent behavior.

## Current big tasks

1. Measure production-policy feedback and harden the concrete Linux/Docker workloads,
   including independent failure detection, exact reviews, convergence and recovery.
   A real playbook migration must declare its supported scope before provider expansion.
2. Enforce and inspect the charter's closed, explicitly bound and open future-dependency
   boundaries. Shared immutable storage does not establish environmental closure.
3. Extend reversible/distributed execution only for concrete workloads. Independent
   remote restoration, same-target coordination/fencing, fleet recovery and secret-bearing
   ordinary data remain separate obligations; coordinated peers have a joint snapshot boundary.
   The charter's "beyond the joint restoration boundary" checklist gates and tracks them.
4. Preserve the explicit YAML representation model and bounded source evidence as syntax
   and tooling evolve. Do not turn provenance into unbounded execution history.

5. Measure agent usability with repeatable model coding tasks;
   [benchmarks/agent/README.md](benchmarks/agent/README.md) owns the experiment protocol.
