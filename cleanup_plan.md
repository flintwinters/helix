# C++ Runtime Cleanup Plan

This document records a semantic-preserving cleanup plan for the current native runtime scaffold. The goal is not to widen functionality. The goal is to make the existing implementation smaller, more uniform, and easier to extend without duplicating logic.

## Constraints

- Preserve current runtime behavior.
- Keep changes incremental.
- Prefer moving logic to the most natural abstraction boundary.
- Reduce repeated structural patterns before adding more builtins.
- Keep the evaluator model aligned with the current project direction: builtins operate on a VM object graph and return cells, including signal cells.

## Current Problems

The current implementation works, but several patterns are duplicated or placed at the wrong abstraction level.

### 1. Callable state is duplicated

`FunCell` is already identified by `Cell::Type::function`, but `Cell` also carries a separate `callable` boolean. That duplicates state and creates the possibility of inconsistency.

### 2. Root-VM validation is local to builtins

`builtins.cpp` contains `expect_root_vm()`, but that check is a shared runtime invariant rather than builtin-specific logic.

### 3. Name lookup is split across too many small helpers

`helix.cpp` currently uses a chain of narrowly scoped lookup helpers:

- `lookup_map_child()`
- `enclosing_map()`
- `lookup_dotted_name_from()`
- `lookup_name_in_map()`
- `lookup_name_from_context()`

These helpers implement one conceptual operation: resolve a name from a context under local, dotted, and parent-scope rules. The logic is mathematically one recursive relation and should be expressed that way.

### 4. Parent attachment is not owned by structure types

`attach_parent_if_missing()` and `set_map_field()` live outside the cell hierarchy even though they enforce structural invariants on maps. This causes object-graph maintenance to leak into evaluator and builtin code.

### 5. Builtins repeat subexpression evaluation patterns

The builtins repeatedly:

- evaluate a child node
- check for `ErrCell`
- continue with a refined assumption

That pattern should be reduced before more builtins are added.

### 6. Finished-state construction lives in the evaluator

`make_finished_state()` and the final `"state"` attachment are runtime-state construction logic, not core evaluation logic. They can be made more uniform and more reusable.

### 7. Runtime dispatch still mixes language errors and C++ exceptions

At the language boundary, the runtime has been moving toward `ErrCell` values instead of C++ exceptions. The remaining control path should reflect that consistently.

## Cleanup Sequence

The changes should be executed in the following order to minimize risk.

## Step 1: Remove duplicated callable state

### Change

- Remove `Cell::callable`.
- Treat `Cell::Type::function` as the unique indicator that a cell is callable.

### Why

- Reduces mutable state.
- Removes one source of inconsistency.
- Simplifies dispatch reasoning.

### Expected code impact

- `src/core.hpp`
- `src/core.cpp`
- `src/helix.cpp`

## Step 2: Move root-VM validation into shared runtime helpers

### Change

- Move the `MapCell` VM expectation helper into `core`.
- Reuse it from builtins and elsewhere.

### Why

- A builtin should not own VM-type validation semantics.
- Later runtime code will need the same check.

### Expected code impact

- `src/core.hpp`
- `src/core.cpp`
- `src/builtins.cpp`

## Step 3: Move map field attachment into `MapCell`

### Change

- Give `MapCell` a mutator that installs a child and attaches its parent if needed.
- Replace external `set_map_field()` usage with that method where possible.

### Why

- Parent-link maintenance belongs to the structure that owns the child relation.
- This reduces repeated structural code across runtime layers.

### Expected code impact

- `src/core.hpp`
- `src/core.cpp`
- `src/builtins.cpp`
- `src/helix.cpp`

## Step 4: Collapse lookup helpers into a smaller resolver surface

### Change

- Replace the current lookup stack with a tighter pair of operations:
  - resolve inside one map
  - resolve from a context through parent ascent

### Why

- The current helper layering is more fragmented than the underlying semantics.
- A smaller resolver surface will make future `eval`, `include`, and nested execution easier to reason about.

### Expected code impact

- `src/helix.cpp`

## Step 5: Reduce builtin evaluation boilerplate

### Change

- Introduce one or two small shared builtin helpers for:
  - evaluating a child expression and propagating `ErrCell`
  - evaluating an integer operand

### Why

- `show`, `add`, and `set` already repeat this pattern.
- Future builtins would otherwise expand duplication.

### Expected code impact

- `src/builtins.cpp`
- possibly `src/core.hpp`
- possibly `src/core.cpp`

## Step 6: Centralize finished-state writeback

### Change

- Move finished-state construction and result attachment into shared helpers or a smaller local finalization unit.

### Why

- `run_main()` should primarily express control flow.
- State-object construction is a separate concern.

### Expected code impact

- `src/core.hpp`
- `src/core.cpp`
- `src/helix.cpp`

## Step 7: Reduce remaining runtime exception dependence

### Change

- Ensure dispatch failures that correspond to language-level failures resolve into `ErrCell` paths where practical.
- Leave process-level failures, such as invalid CLI invocation or YAML parsing failure, at the C++ level.

### Why

- The runtime should distinguish host failure from language failure.
- This will matter more once `Signal` and `Return` are added.

### Expected code impact

- `src/core.cpp`
- `src/helix.cpp`

## Step 8: Refresh invocation documentation

### Change

- Update `cpp_invo_doc.md` after the runtime surface settles.

### Why

- The invocation graph should reflect actual runtime structure after cleanup.

## Success Criteria

The cleanup is successful if:

- the runtime still passes the same implemented fixture set
- the main evaluator is smaller and easier to read
- builtin code loses repeated subexpression boilerplate
- parent-link maintenance becomes more local to structure types
- runtime dispatch relies on fewer duplicated helper layers
