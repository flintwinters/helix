# Helix

## Overview

Helix is a YAML-shaped language and VM.

- A program is a top-level mapping.
- `main` is the required entrypoint.
- Execution state lives inside the same mapping under `state`.
- Strings can be symbolic references.
- Sequences can be executable forms.

The current C++ runtime is VM-oriented rather than environment-oriented: builtins receive the current VM, decide which arguments to evaluate, and may mutate the VM directly.

## Data Model

- YAML mappings become runtime maps.
- YAML sequences become runtime vectors.
- YAML strings can name builtins or fields.
- Integers are integers.
- `null` is nil.

Example:

```yaml
seed: 10
delta: 2
main: [set, total, [add, seed, delta]]
```

## Name Resolution

Resolution is simple and centralized.

- Builtin names resolve first.
- Then exact keys in the current/root VM.
- Dotted paths such as `foo.bar.baz` walk nested mappings.

There is no general prototype-style parent-chain lookup yet.

## Evaluation

Evaluation rules are small.

- Strings resolve by name.
- Non-sequence scalars evaluate to themselves.
- A sequence is treated as a form.
- The first element of a form is the actor.
- The remaining elements are arguments.

Actors must resolve to builtins in the current runtime.

## VM State

The runtime stores scheduler state under `state`.

Common fields:

- `status`: usually `ready`, `running`, `finished`, or `error`
- `frames`: resumable execution frames
- `result`: terminal result
- `error`: surfaced failure text when present

## Sequencing

Helix sequencing is centered on `list`, `step`, and `start`.

- `list` arms a vector for stepped execution.
- `step` advances a named child VM once.
- `start` runs a named child VM until it reaches a terminal state.

In practice:

- `main: [list, steps]` declares a resumable instruction stream.
- repeated `[step, child_vm_name]` walks that stream.
- `[start, child_vm_name]` runs the whole child VM to completion.

## Includes

At the root VM level, `include` may name YAML files to load.

```yaml
include: [shared.yaml]
```

Each included file is loaded and bound into the root VM under the include file stem.

## Builtins

All current builtin calls are YAML vectors.

## `show`

Form:

```yaml
[show, expr]
```

- Evaluates `expr`
- Renders the value to stdout
- Returns `null`

Use it for inspection, not transformation.

## `add`

Form:

```yaml
[add, left, right]
```

- Evaluates both operands
- Requires integers
- Returns `left + right`

## `sub`

Form:

```yaml
[sub, left, right]
```

- Evaluates both operands
- Requires integers
- Returns `left - right`

## `mul`

Form:

```yaml
[mul, left, right]
```

- Evaluates both operands
- Requires integers
- Returns `left * right`

## `set`

Form:

```yaml
[set, name, expr]
```

- `name` must be a string literal
- Evaluates `expr`
- Stores the result in the current VM under `name`
- Returns the stored value

`set` writes only by direct field name. It does not assign through dotted paths.

## `eval`

Form:

```yaml
[eval, expr]
```

- Evaluates `expr`
- Evaluates the resulting value again as code
- Returns that second evaluation result

Use it when code is stored indirectly.

## `list`

Form:

```yaml
[list, sequence_name]
```

- Resolves `sequence_name` to a vector
- Arms the current VM for stepped execution over that vector
- Returns `null`

This is the sequencing primitive for resumable VMs.

## `append`

Form:

```yaml
[append, sequence_name, expr]
```

- Resolves `sequence_name` to a vector target
- Evaluates `expr`
- Pushes the value onto the vector
- Returns the mutated vector

## `pop`

Form:

```yaml
[pop, sequence_name]
```

- Resolves `sequence_name` to a vector target
- Removes the last element
- Returns the removed value
- Errors on an empty vector

## `at`

Form:

```yaml
[at, sequence_name, index_expr]
```

- Resolves `sequence_name` to a vector target
- Evaluates `index_expr`
- Requires an integer index
- Returns the element at that index
- Errors on out-of-bounds access

## `copy`

Form:

```yaml
[copy, value_name]
```

- Resolves the argument without evaluating it as code
- Copies vectors and maps shallowly
- Returns scalars unchanged

Use it when you need a new container rather than an alias to the same one.

## `if`

Form:

```yaml
[if, condition, then_expr, else_expr]
```

- Evaluates `condition`
- Truthiness rules are minimal:
- `null` is false
- integer `0` is false
- everything else is true
- Evaluates and returns either `then_expr` or `else_expr`

Only the selected branch is evaluated.

## `while`

Form:

```yaml
[while, condition, body_sequence_name]
```

- Re-evaluates `condition` before each iteration
- Resolves `body_sequence_name` to a vector body
- Executes each body element in order
- Stops when the condition becomes false
- Returns `null`

The body argument is a named vector, not an inline statement block.

## `start`

Form:

```yaml
[start, child_vm_name]
```

- Resolves `child_vm_name` to a nested VM mapping
- Repeatedly advances that child VM
- Stops only when the child reaches a terminal state or signals
- Returns the child VM's final step result

Use `start` for full child execution.

## `step`

Form:

```yaml
[step, child_vm_name]
```

- Resolves `child_vm_name` to a nested VM mapping
- Advances that child VM once through the runtime scheduler
- Returns the child VM status cell

Use `step` for debugger-like or cooperative progression of a child VM.

## Minimal Patterns

Single expression program:

```yaml
left: 20
right: 22
main: [add, left, right]
```

Stepped child VM:

```yaml
child:
  seed: 10
  delta: 2
  steps:
    - [set, first, [add, seed, delta]]
    - [set, second, [add, first, delta]]
  main: [list, steps]
items:
  - [step, child]
  - [step, child]
main: [list, items]
```

## Current Limits

- Only builtins are callable.
- Root include loading is special-cased.
- Error/signal handling is still partial and host-driven.
- Sequence stepping semantics are runtime-specific and still evolving.
