# HCC Development Cockpit

## Mission

HCC exists to bring Helix's debugging power to C programs. It compiles C into
readable Helix object graphs so execution can be inspected, stepped, persisted,
edited, versioned, branched, and replayed through the Helix runtime.

The motivating applications are rigorous microcontroller testing before
flashing hardware and reproducible emulation of distributed-system field tests.
Fast or compact generated code is secondary to faithful, observable execution.

## Governing Direction

HCC is successful when debugging compiled C through Helix feels like debugging
the C program itself. Readable YAML is a means to that end, not the end itself.

Development priorities, in order:

1. Preserve C behavior.
2. Preserve the identity and provenance of source-level operations.
3. Expose C state and control flow clearly through Helix.
4. Make executions deterministic and reproducible.
5. Expand the supported C subset without weakening the preceding properties.

Do not add a C construct merely because it can be translated. Its generated
state, stepping behavior, failures, and source correspondence must also be
understandable.

## Debugging Invariants

- Every executable generated form must be traceable to its originating C source
  location.
- A debugger must be able to present C functions, calls, scopes, variables, and
  control flow without reverse-engineering incidental YAML layout.
- Persisted or branched VM state must retain enough provenance to resume and
  explain the corresponding C execution.
- Lowering must preserve C evaluation order and control-flow semantics. A more
  readable but semantically incorrect translation is unacceptable.
- Compiler failures must identify the unsupported C construct and its source
  location.
- Generated YAML remains deterministic, human-readable, and directly executable
  by Helix.
- Tests for a supported construct must assert emitted structure, terminal state,
  and debugging behavior relevant to that construct.

## Current Snapshot

As of 2026-07-19, HCC is an executable vertical slice rather than a C debugger.

Implemented:

- `pycparser` parsing, with optional platform preprocessing
- `int` functions, parameters, and local variables
- function calls and returns
- assignment and arithmetic expressions
- compound assignment and unary `+`/`-`
- `if`, `while`, integer casts, and ternary expressions
- readable typed Helix function objects
- exact YAML and final VM-state fixture assertions

Verified:

- `uv run python manage.py hcc-test`: 3 passed, 0 failed
- fixtures cover function calls, `if`/`else`, and a `while` loop

Critical debugging gap:

- Generated Helix does not preserve C source provenance. Helix can step and
  inspect the generated graph, but it cannot yet relate frames, forms, or errors
  back to the original C source. This is the next architectural checkpoint.

Known semantic and coverage gaps:

- comparisons and logical operators
- C short-circuit and complete evaluation-order coverage
- multi-statement `if` branches in expression position
- `for`, `do`, `switch`, `break`, and `continue`
- integer widths, signedness, promotion, conversion, and overflow behavior
- pointers, arrays, structs, unions, enums, globals, and static storage
- allocation, libc, volatile data, and hardware-facing boundaries
- explicit validation of the required `main` signature
- negative compiler tests and source-aware debugging tests

## Active Checkpoint: C Source Provenance

Define and implement one canonical provenance path from `pycparser` coordinates
through generated YAML and the Helix cell graph to runtime frames, errors, and
debugger presentation.

Acceptance criteria:

- generated executable forms retain original C file, line, and column identity
- stepping can report the active C source location
- runtime errors in generated programs report the responsible C location
- provenance survives YAML serialization and resumed execution
- lowering one C expression into multiple Helix forms has an explicit,
  deterministic source-mapping rule
- fixtures verify provenance rather than only generated YAML and final results
- provenance is represented once and consumed by shared runtime/debugging helpers

Open design question:

- Determine whether C provenance extends the runtime's existing source-location
  model or requires a distinct origin-location field. Resolve this from the
  current `Cell` and YAML source-location contracts before choosing names or
  representation.

## Roadmap

### 1. Establish debugging identity

- complete the active provenance checkpoint
- define C-aware frame, scope, and variable presentation
- make breakpoints addressable by C source location
- verify persisted and branched executions retain C debugging identity

### 2. Complete structured scalar C

- add comparisons and logical operators with correct short-circuit behavior
- support complete compound blocks and structured loop control
- define integer types, conversions, and arithmetic semantics explicitly
- add positive, negative, stepping, error, and resume fixtures per construct

### 3. Model freestanding C state

- add globals and static storage
- add arrays, pointers, structs, unions, and enums through inspectable Helix state
- preserve aliasing and object lifetime without hiding them behind opaque handles
- model `volatile` and external effects as explicit debugging boundaries

### 4. Exercise motivating systems

- run representative microcontroller logic in Helix before flashing
- make hardware inputs and outputs reproducible fixtures
- model multiple C components as branchable Helix MicroVMs
- reproduce and inspect distributed field scenarios from persisted state

Optimization and broad libc compatibility remain deferred until these workflows
demonstrate strong debugging value.

## Generated-Code Policy

- Preserve source names and structured C control flow where semantics allow.
- Emit named Helix functions with `type`, `params`, and `body`.
- Avoid anonymous temporaries unless required to preserve C evaluation order.
- Prefer existing Helix forms and runtime facilities over parallel compiler-only
  machinery.
- Introduce lower-level blocks or control-flow IR only when structured Helix
  cannot faithfully express C semantics.
- Treat generated YAML shape and debugging metadata as tested public contracts.

## Verification Contract

The canonical command is:

```bash
uv run python manage.py hcc-test
```

Every supported construct should eventually be tested across four observable
dimensions:

1. deterministic emitted Helix YAML
2. correct completed VM state
3. correct intermediate stepping and resumable state
4. correct C source correspondence for frames, variables, and errors

Use the narrowest relevant fixture during development, then run the complete HCC
suite before committing. Run the runtime suite when a shared runtime, source
location, frame, serialization, or debugger contract changes.

## Cockpit Maintenance

Update this file at every verified HCC checkpoint:

- advance the active checkpoint and its acceptance criteria
- record newly implemented and verified behavior in the current snapshot
- remove resolved gaps and add newly discovered constraints
- keep the roadmap ordered by debugging value
- record architectural decisions that constrain later lowering or debugging work

Keep detailed implementation history in Git commits. Keep this cockpit concise,
current, and sufficient to recover HCC's purpose, state, and immediate direction
in a new development context.

## Decision Log

- 2026-07-19: Established HCC's primary purpose as leveraging Helix's debugging
  capabilities for C programs. Readable lowering remains essential but is
  subordinate to debugging fidelity.
- 2026-07-19: Made C source provenance the next architectural checkpoint before
  substantially expanding language coverage.
