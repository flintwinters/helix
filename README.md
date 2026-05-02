## Overview

The Helix programming system is mainly inspired by Smalltalk and defines a language where the primitive runtime unit is a cell. Cells are analogous to Lisp nodes: larger program structures are composed of many cells rather than collapsing into one indivisible runtime object. There are no distinct runtime categories such as function, environment, object, or VM. Instead, these roles emerge from how cells are connected and how composite structures behave.

The program is therefore a rooted graph of cells. At the top level it may appear as one YAML object, but semantically that object is a composite built from many constituent cells. Execution begins by locating a `"main"` entrypoint within that graph. From that point forward, all computation proceeds through two operations:

- `find(vm, key)` performs name resolution relative to a given execution context.
- `eval(vm)` executes a cell within that same context.

The crucial simplification is that the environment and the VM are identical at the level of the cell graph. There is no separate environment object standing apart from execution state. Lexical scope, dynamic scope, and runtime state all live in the same composed structure.

## Name Resolution

Name resolution is purely structural.

- A cell may contain arbitrary keys.
- If a key is not found locally, `find` follows a `"parent"` reference recursively.
- Any cell can serve as a scope simply by participating in this delegation chain.

This creates a prototype-like structure that simultaneously models lexical scope and inheritance. No special environment object exists apart from the graph being traversed. Because `find` is the only retrieval mechanism, it becomes the backbone of symbolic reference.

Strings are therefore not merely primitive values. A `StrCell` can act as a symbolic reference: when evaluated, it resolves its contents through `find`. This removes the need for a separate symbol type and keeps the surface representation aligned with runtime behavior.

## Evaluation Model

Evaluation is driven by polymorphism on cell type. There is no global interpreter loop that distinguishes expressions from values. Instead, each cell defines how it evaluates itself.

`VecCell` encodes structured computation using a Lisp-like rule:

- the first element is treated as the actor
- the remaining elements are treated as arguments

However, arguments are not passed explicitly through ordinary host-language function parameters. Instead, they are mediated through VM state. The `eval(vm)` method of each cell reads from and writes to the executing VM, typically through a `"state"` field accessible via `find`.

This means each executable cell is best understood as a rule for transforming VM state. Builtins in particular do not receive a detached argument environment and return a separate updated machine. They operate on the executing VM directly and therefore behave like functions from VM state to VM state, with the transition expressed implicitly through in-place mutation of the current composite VM structure.

Stacks and frames may still appear as internal conventions inside `"state"`, but they are implementation details of how a particular cell organizes execution, not the primary abstraction of the language.

The separation between the two primitive operations is intentional:

- `find` is observational and traverses structure
- `eval` is effectful and mutates execution state

## Calling Convention And State

The absence of explicit argument passing has important consequences.

- There is no fixed calling convention at the interface level.
- Different cells may interpret VM state differently.
- Conventions about local state layout are runtime agreements rather than static type rules.

This gives the system flexibility. One cell may organize its local state around a stack discipline, while another may use named fields, frame-like records, or some other structure under `"state"`. The tradeoff is that data flow becomes more implicit, so evaluation discipline must be maintained by convention. The important constant is not a specific stack protocol, but the fact that evaluation proceeds by reading and updating the current VM.

## Nested Execution

Any cell can act as a micro-VM.

If a cell defines a `"state"` field and implements `eval` in terms of that state, it can host its own execution process. That supports:

- nested interpreters
- coroutines
- isolated execution contexts

Because `find` always operates relative to the current VM, switching execution contexts is as simple as changing which cell is passed as `vm`. Execution is therefore distributed across cells that locally manage their own semantics rather than centralized in one fixed interpreter object.

## Error And Return Semantics

Error handling is integrated into the object model. Errors are represented as cells rather than host-language exceptions or unrelated out-of-band mechanisms. When an operation fails, it produces an error cell, and subsequent `find` or `eval` operations propagate that failure through ordinary cell behavior.

`return` introduces a different problem. A returned value is ordinary data, but the act of returning is not. If `return` were modeled as just another normal result of `eval`, then every caller of `eval` would need to inspect the result to determine whether evaluation produced data or a non-local control transfer. That would force control-flow checks into:

- every sequencing site
- every builtin that evaluates subexpressions
- every future evaluator helper

This would spread one control concern across the entire runtime and weaken the intended uniformity of the `find`/`eval` model.

The planned solution is to introduce a `Signal` parent cell type for non-local control outcomes, with `Error` and `Return` as sibling signal cells.

- `Signal` keeps control outcomes within the cell model.
- `Error` represents failure.
- `Return` represents successful non-local transfer.

This keeps the representation Smalltalk-like and inspectable while preserving proper control behavior. The runtime will distinguish between:

- a signal cell as an object
- an actively signaled condition in VM state

A `Return` cell may therefore exist as structured data, but when `return` is evaluated in control position it activates a return signal in VM state. Evaluation then unwinds until the appropriate function boundary consumes that signal, just as an active error signal propagates until it is handled or reaches the top level.

This separation is necessary for two reasons:

- Reification: cells are the only runtime entities, so errors and returns must be representable and inspectable as cells.
- Non-local transfer: `return` is not merely a value constructor; it is a request to terminate the current evaluation path.

The `Signal` family solves both requirements without forcing every builtin and every caller of `eval` to thread control-flow tags through ordinary value flow.

## Surface Syntax

The system’s cell-based representation aligns naturally with YAML as the surface syntax.

- a YAML mapping becomes a composite mapping structure made of cells
- a YAML sequence becomes a composite vector structure made of cells
- individual mapping entries, sequence elements, and strings are represented by cells
- a string becomes a `StrCell` that may resolve symbolically

This removes the need for a custom parser and keeps the syntax close to the runtime representation. Programs can be written directly as YAML documents, and the parsed structure already mirrors the underlying graph of cells rather than requiring a separate surface-language AST.

## Conceptual Lineage

Conceptually, the system sits at the intersection of several traditions:

- prototype-based object systems, through delegation via `"parent"`
- Lisp-style evaluation, through structured data representing computation
- stateful virtual machines, through implicit argument passing and VM-driven execution

Helix does not fully adopt any one of these paradigms. Instead, it extracts their minimal operational principles and recombines them into a uniform model centered on cells, `find`, and `eval`.

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
