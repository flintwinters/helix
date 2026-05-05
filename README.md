## Overview

The Helix programming system is mainly inspired by Smalltalk, but the current implementation is a Racket prototype over parsed YAML values. In the current runtime, the executable program is a mutable top-level mapping with a required `"main"` entrypoint and a runtime-owned `"state"` field. Strings, lists, mappings, and scalars are interpreted directly through host-language values rather than through a completed C++-style cell class hierarchy.

Even so, the design direction is already visible. Helix is trying to converge on a model where small runtime units behave like cells or Lisp nodes, while larger runtime objects such as a running VM are assembled from those units rather than treated as unrelated machinery.

Execution begins by locating `"main"` inside the top-level program mapping. From that point forward, the current runtime is organized around two operations:

- `find(vm, key)` performs name resolution relative to a given execution context.
- `eval(vm)` executes a cell within that same context.

In the present Racket implementation these operations are realized concretely as the `resolve` and `evaluate` functions, together with a small amount of VM scheduling logic in `helix.rkt`. The crucial simplification is that execution state lives inside the same top-level program object being interpreted rather than in a detached host-side environment.

## Name Resolution

The current name-resolution behavior is simpler than the long-term prototype-style design.

- Builtin names resolve first.
- Then top-level program fields resolve by exact key.
- Dotted names such as `foo.bar` resolve by walking nested mappings from the program root.

There is not yet a general `"parent"`-chain lookup in the Racket runtime. That remains part of the conceptual direction rather than current behavior. Today, symbolic reference is still centralized enough that `resolve` forms the backbone of evaluation.

Strings are therefore not merely primitive values. A `StrCell` can act as a symbolic reference: when evaluated, it resolves its contents through `find`. This removes the need for a separate symbol type and keeps the surface representation aligned with runtime behavior.

## Evaluation Model

The current Racket runtime uses a small centralized evaluator rather than a fully distributed per-cell method system. Concretely:

- strings are resolved through `resolve`
- lists are treated as executable forms
- all other YAML values evaluate to themselves

`VecCell` encodes structured computation using a Lisp-like rule:

- the first element is treated as the actor
- the remaining elements are treated as arguments

The actor must resolve to a builtin procedure. There is not yet a user-defined callable cell protocol in the current runtime. Even so, the evaluator already has the shape the system is moving toward: evaluation happens against the current program/VM object, and builtins decide when to evaluate their arguments.

This means that executable forms, and builtins in particular, are best understood as state transformers over the active VM. A builtin does not receive a detached argument environment and return a separately constructed machine. It receives unevaluated argument nodes together with the current program object and updates that program in place. Semantically, builtin execution behaves like a transition from one VM state to the next, even though that transition is realized by mutating the current VM object rather than allocating a new one.

The long-term direction is more cell-distributed than the current Racket prototype, but the present implementation already preserves the key operational point: evaluation is rooted in the running VM, not in a separate detached environment.

The separation between the two primitive operations is intentional:

- `find` is observational and traverses structure
- `eval` is effectful and mutates execution state

## Sequence Semantics

`list` is the current sequencing form. At its core it is simple: it takes a vector, loops over its elements from left to right, and evaluates each one against the active VM.

- Each element is evaluated in order.
- Earlier elements may mutate the VM.
- Later elements observe those mutations.
- The result is the last completed value, or `null` for an empty sequence.

That is the whole basic rule. `list` is therefore the builtin that gives Helix ordinary sequential execution.

Stepped execution does not change that rule. The stepped and non-stepped modes run the same loop and differ only in where control yields back to the scheduler.

So:

- `list` defines evaluation order
- stepping defines yield points

This is also why `list` matters for the planned `return` model. `return` should simply stop that loop early. In the planned signal-based design:

- `list` keeps running elements until one activates a control signal
- `Return` stops the loop immediately
- the active return signal then propagates to the proper VM boundary

So `list` stays simple even as VM control flow becomes richer. It is still just the sequence loop.
## Calling Convention And State

The current builtins make the VM-oriented calling convention concrete.

- Builtins receive raw argument nodes.
- Builtins explicitly call `evaluate` on the arguments they want to force.
- Builtins can mutate the current program mapping directly.
- Runtime scheduling state is stored under `"state"`.

In the existing Racket runtime, `"state"` currently carries at least:

- `"status"` for `ready`, `running`, `finished`, or `error`
- `"frames"` for resumable stepped execution
- `"result"` for terminal results
- `"error"` for surfaced runtime failure text

This is why the current system is better described as VM-driven than stack-driven. Frames exist, but they are only one field inside the broader mutable runtime state.

## Nested Execution

The current implementation already supports nested execution, but in a narrower form than the full conceptual model. Specifically, `start` and `step` resolve a named nested VM mapping and run it through the same scheduler used by the outer runtime. That supports:

- nested interpreters
- resumable child execution
- isolated child state within each nested VM's `"state"` mapping

The broader idea that any arbitrary cell can host its own evaluator remains part of the direction of the system, but the implemented path today is specifically nested VM mappings driven by `start`, `step`, `advance-vm!`, and `run-vm`.

## Error And Return Semantics

The current Racket runtime does not yet implement first-class `Error`, `Signal`, or `Return` cells. Operationally, failures are reported through Racket exceptions, and the VM mirrors those failures into `"state"` by setting `"status"` to `"error"` and recording an `"error"` message.

That said, clean first-class errors remain an important design goal for Helix, because they would let failure participate in the same object model as ordinary values rather than escaping into host-language control flow.

`return` introduces a different problem. A returned value is ordinary data, but the act of returning is not. If `return` were modeled as just another normal result of `eval`, then every caller of `eval` would need to inspect the result to determine whether evaluation produced data or a non-local control transfer. That would force control-flow checks into:

- every sequencing site
- every builtin that evaluates subexpressions
- every future evaluator helper

This would spread one control concern across the entire runtime and weaken the intended uniformity of the evaluator.

The planned solution is to introduce a `Signal` parent cell type for non-local control outcomes, with `Error` and `Return` as sibling signal cells.

- `Signal` keeps control outcomes within the cell model.
- `Error` represents failure.
- `Return` represents successful non-local transfer.

This keeps the representation Smalltalk-like and inspectable while preserving proper control behavior. It is a direction for the runtime rather than a description of functionality that already exists today. In that planned model, the runtime would distinguish between:

- a signal cell as an object
- an actively signaled condition in VM state

A `Return` cell may therefore exist as structured data, but when `return` is evaluated in control position it activates a return signal in VM state. Evaluation then unwinds until the appropriate function boundary consumes that signal, just as an active error signal propagates until it is handled or reaches the top level.

This separation is necessary for two reasons:

- Reification: cells are the only runtime entities, so errors and returns must be representable and inspectable as cells.
- Non-local transfer: `return` is not merely a value constructor; it is a request to terminate the current evaluation path.

The `Signal` family solves both requirements without forcing every builtin and every caller of `eval` to thread ad hoc control-flow tags through ordinary value flow.

## Surface Syntax

The current implementation aligns naturally with YAML as the surface syntax.

- a YAML mapping becomes a `MapCell`
- a YAML sequence becomes a `VecCell`
- a string becomes a `StrCell` that may resolve symbolically

More precisely, the Racket prototype parses YAML directly into host mappings, lists, strings, and scalars, and then interprets those values with Helix semantics. This removes the need for a custom parser and keeps the syntax close to the runtime representation while the cell model is still being made more explicit in the implementation.

## Conceptual Lineage

Conceptually, the system sits at the intersection of several traditions:

- prototype-based object systems, through delegation via `"parent"`
- Lisp-style evaluation, through structured data representing computation
- stateful virtual machines, through implicit argument passing and VM-driven execution

The current Racket runtime does not fully realize all of those ideas yet. Instead, it should be read as a working interpreter that already demonstrates YAML-backed evaluation, builtin dispatch, mutable VM state, includes, and stepped child execution, while still pointing toward a more explicit cell-oriented object model.

#### Current racket shortcomings

- General "parent"-chain lookup is not implemented; current resolution is builtin-first, then top-level key, then dotted path.
- First-class Error, Signal, and Return cells are not implemented yet; current failures are Racket exceptions mirrored into state.error.
- A distributed per-cell method evaluator is not implemented yet; the current runtime uses centralized resolve / evaluate.
- Names like MapCell, VecCell, and StrCell are design-language, not current Racket runtime types.

## Codebase overview

The diagram below shows the current repository-level invocation graph. Solid edges are unconditional calls in the relevant control path; dotted edges are branch-dependent or optional calls. Root and leaf highlighting is computed within the repository function graph, not including top-level forms or library calls.

```mermaid
flowchart LR
  classDef root fill:#447,stroke:#1f6feb,stroke-width:2px;
  classDef leaf fill:#475,stroke:#2da44e,stroke-width:2px;
  classDef rootleaf fill:#764,stroke:#9a6700,stroke-width:2px;

  subgraph H["helix.rkt"]
    h_remember["remember-vm-source!<br/>tag VM with source dir"]
    h_vm_base["vm-base-directory<br/>read VM source dir"]
    h_load_yaml["load-yaml-file<br/>load YAML from disk"]
    h_load_program["load-program<br/>load and validate root VM"]
    h_load_include["load-include-entry<br/>load include and derive key"]
    h_apply_includes["apply-includes!<br/>merge include files into VM"]
    h_expect_vm["expect-vm<br/>validate VM-shaped mapping"]
    h_ensure_state["ensure-vm-state!<br/>create VM state fields"]
    h_reset_state["reset-vm-state!<br/>clear transient run state"]
    h_initialize["initialize-vm!<br/>prepare VM for execution"]
    h_mark_error["mark-vm-error!<br/>record VM failure state"]
    h_vm_frames["vm-frames<br/>read frame stack"]
    h_set_frames["set-vm-frames!<br/>write frame stack"]
    h_vm_status["vm-status-result<br/>decode terminal VM state"]
    h_finish["finish-vm!<br/>store finished result"]
    h_with_running["with-vm-running-state<br/>run only while active"]
    h_resolve_child["resolve-child-vm<br/>resolve nested VM argument"]
    h_with_child["with-resolved-child-vm<br/>resolve child then continue"]
    h_advance["advance-vm!<br/>run or resume one VM step"]
    h_run["run-vm<br/>drive VM to completion"]
    h_builtin_start["builtin-start<br/>run named child VM"]
    h_builtin_step["builtin-step<br/>step named child VM"]
  end

  subgraph B["builtins.rkt"]
    b_expect_arity["expect-arity<br/>check builtin arity"]
    b_expect_number["expect-number<br/>check numeric value"]
    b_expect_string["expect-string<br/>check string value"]
    b_call_param["call-with-step-parameter<br/>bind step parameter"]
    b_call_resolve["call-with-resolve<br/>bind external builtin hook"]
    b_call_runtime["call-with-step-runtime<br/>bind step runtime"]
    b_call_frame["call-with-step-frame<br/>bind step frame"]
    b_frame_ref["frame-ref<br/>read frame field or default"]
    b_cells_yaml["cells->yaml-string<br/>render VM cells as YAML"]
    b_append_stdout["append-stdout!<br/>append rendered stdout line"]
    b_eval_sequence["evaluate-sequence<br/>run sequence with yields"]
    b_resolve_path["resolve-path<br/>follow dotted path"]
    b_resolve["resolve<br/>resolve builtin or program value"]
    b_evaluate["evaluate<br/>evaluate one node"]
    b_evaluate_list["evaluate-list<br/>dispatch vector actor"]
    b_builtin_add["builtin-add<br/>evaluate and add two numbers"]
    b_builtin_eval["builtin-eval<br/>evaluate evaluated code"]
    b_builtin_list["builtin-list<br/>evaluate sequence value"]
    b_builtin_show["builtin-show<br/>print and preserve value"]
    b_builtin_set["builtin-set<br/>store field in VM"]
    b_builtin_q["builtin?<br/>test builtin membership"]
    b_resolve_builtin["resolve-builtin<br/>bind builtin dispatcher"]
  end

  h_load_program --> h_load_yaml
  h_load_yaml --> h_remember
  h_load_include --> h_load_yaml
  h_apply_includes -.-> h_vm_base
  h_apply_includes -.-> h_load_include
  h_initialize --> h_ensure_state
  h_initialize -.-> h_apply_includes
  h_initialize -.-> h_reset_state
  h_mark_error --> h_ensure_state
  h_vm_frames --> h_ensure_state
  h_set_frames --> h_ensure_state
  h_with_running --> h_ensure_state
  h_with_running --> h_vm_status
  h_resolve_child --> b_expect_arity
  h_resolve_child --> h_expect_vm
  h_resolve_child --> b_resolve
  h_resolve_child --> b_expect_string
  h_resolve_child --> h_remember
  h_resolve_child --> h_vm_base
  h_with_child --> h_resolve_child
  h_advance --> h_expect_vm
  h_advance -.-> h_mark_error
  h_advance --> h_initialize
  h_advance --> h_with_running
  h_advance --> b_call_resolve
  h_advance --> b_call_runtime
  h_advance -.-> h_vm_frames
  h_advance -.-> h_set_frames
  h_advance -.-> b_call_frame
  h_advance -.-> b_evaluate
  h_advance -.-> h_finish
  h_run --> h_expect_vm
  h_run --> h_with_running
  h_run --> h_advance
  h_builtin_start --> h_with_child
  h_builtin_start --> h_run
  h_builtin_step --> h_with_child
  h_builtin_step --> h_advance
  h_builtin_step --> h_ensure_state

  b_call_resolve --> b_call_param
  b_call_runtime --> b_call_param
  b_call_frame --> b_call_param
  b_eval_sequence --> b_frame_ref
  b_eval_sequence --> b_evaluate
  b_resolve -.-> b_resolve_builtin
  b_resolve -.-> b_resolve_path
  b_evaluate -.-> b_resolve
  b_evaluate -.-> b_evaluate_list
  b_evaluate_list --> b_evaluate
  b_builtin_add --> b_expect_arity
  b_builtin_add --> b_evaluate
  b_builtin_add --> b_expect_number
  b_builtin_eval --> b_expect_arity
  b_builtin_eval --> b_evaluate
  b_builtin_list --> b_expect_arity
  b_builtin_list -.-> b_evaluate
  b_builtin_list --> b_frame_ref
  b_builtin_list --> b_eval_sequence
  b_builtin_show --> b_expect_arity
  b_builtin_show --> b_evaluate
  b_builtin_show --> b_cells_yaml
  b_builtin_show --> b_append_stdout
  b_builtin_set --> b_expect_arity
  b_builtin_set --> b_expect_string
  b_builtin_set --> b_evaluate

  class h_load_program,h_builtin_start,h_builtin_step root;
  class h_expect_vm,h_reset_state,h_vm_status,h_finish,b_expect_arity,b_expect_number,b_expect_string,b_frame_ref,b_cells_yaml,b_append_stdout,b_resolve_path,b_resolve_builtin leaf;
  class b_builtin_q rootleaf;
```

## Responsibility matrix

This matrix is intentionally selective. It captures the dominant responsibilities rather than every helper edge.

| Func | Includes | Path | Eval | Steps | Child VM Ctrl |
| --- | --- | --- | --- | --- | --- |
| `apply-includes!`         | x |   |   |   |   |
| `initialize-vm!`          | x |   |   |   |   |
| `resolve-path`            |   | x |   |   |   |
| `resolve`                 |   | x | x |   |   |
| `call-with-resolve`       |   | x |   | x |   |
| `evaluate`                |   |   | x |   |   |
| `evaluate-list`           |   |   | x |   |   |
| `advance-vm!`             |   |   | x | x |   |
| `run-vm`                  |   |   |   | x |   |
| `resolve-child-vm`        |   | x |   |   | x |
| `builtin-start`           |   |   |   | x | x |
| `builtin-step`            |   |   |   | x | x |
| `evaluate-sequence`       |   |   | x | x |   |
| `builtin-eval`            |   |   | x |   |   |
| `builtin-list`            |   |   | x | x |   |
