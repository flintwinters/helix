# HCC Plan

HCC is the Helix C compiler. Its first goal is not fast generated code. Its first goal is to emit Helix that a human can read, inspect, step, edit, and understand as idiomatic Helix object graphs.

## Target Philosophy

Generated Helix should preserve the shape and intent of the source C program where possible. Functions should become Helix function objects, local C state should become local Helix scope state, and source-level control flow should lower only as far as needed for clarity.

Prefer obvious Helix forms over compact machine-like encodings. A generated program should be useful as an explanation of the C program, not merely as an executable artifact.

## Near-Term Compiler Shape

The pipeline should remain explicit and inspectable:

- C source
- tokens
- parsed C AST
- typed C AST
- readable Helix IR
- executable Helix program

Each stage should be representable as structured data so the compiler itself can eventually be debugged and versioned through Helix.

## Initial C Subset

Start with a small freestanding subset:

- integer values
- local variables
- function definitions
- function calls
- return statements
- `if`
- `while`
- simple arithmetic and comparisons

Defer pointers, structs, arrays, heap allocation, and libc until the function/scope/control-flow model feels natural.

## Generated Code Style

Emit named functions using `kind: function`, `params`, and `body`.

Use local names that resemble the C source. Avoid anonymous temporaries unless the C expression genuinely needs one to preserve evaluation order.

Prefer structured Helix control forms while they remain readable. Introduce lower-level labels or block IR only when C semantics require it.

## Success Criteria

A small generated program should be understandable without reading the compiler.

Stepping generated Helix should feel like stepping the original C source.

The generated object graph should make scope, function calls, and state changes visible rather than hiding them behind opaque runtime mechanisms.
