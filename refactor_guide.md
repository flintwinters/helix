# Native Refactor Guide

This document records the next code-reuse refactor pass for the native C++ runtime. The goal is not feature expansion. The goal is to reduce duplicated protocol logic, make scheduler behavior more algebraic, and move shared runtime responsibilities into the correct layer.

The items are ordered. Earlier items should be addressed before later ones unless a later item becomes a direct blocker.

## [x] 1. Unify `evaluate_or_signal(...)` and `resolve_or_signal(...)`

This is the highest-priority refactor and should have been done earlier.

Right now [src/builtins.cpp](/home/iron/projects/Helixrkt/src/builtins.cpp:1) still contains two wrappers that are almost the same:

- `evaluate_or_signal(...)`
- `resolve_or_signal(...)`

Both:

- verify that a callback is initialized
- invoke that callback on a node and VM
- immediately propagate any signal cell
- otherwise return the ordinary value

The only real difference is which callback they invoke.

This should become one generic helper parameterized by the callback to invoke. For example, conceptually:

- `apply_vm_callback_or_signal(callback, node, vm, init_error_message)`

Why this matters:

- it removes a repeated control-flow wrapper at the exact point where signal semantics are most important
- it makes signal propagation rules more explicit and centralized
- it lowers the chance that evaluation and resolution drift apart semantically later

Success condition:

- there is one wrapper for “invoke callback and propagate signals”
- builtin code no longer distinguishes this pattern twice

Status:

- completed via `apply_vm_callback_or_signal(...)` in [src/builtins.cpp](/home/iron/projects/Helixrkt/src/builtins.cpp:1)

## [x] 2. Move VM-state helpers out of `helix.cpp` and into shared runtime code

The following helpers currently live in [src/helix.cpp](/home/iron/projects/Helixrkt/src/helix.cpp:1):

- `ensure_vm_state(...)`
- `vm_frames(...)`
- `vm_status(...)`
- `set_vm_status(...)`
- `clear_vm_terminal_fields(...)`
- `vm_result(...)`
- `is_terminal_status(...)`

These are not entrypoint-specific. They are part of the runtime model itself.

They should move into shared runtime code:

- declarations in `core.hpp`
- implementations in `core.cpp`

Why this matters:

- builtins and scheduler code both depend on the same VM protocol
- the VM object model should not be implicitly split between “runtime” and “entrypoint”
- later features like `return`, function frames, and new signals will need this same API

Success condition:

- `helix.cpp` stops owning shared VM-state protocol helpers
- VM state access becomes a reusable runtime layer

Status:

- completed by moving VM-state and terminal-state helpers into [src/core.hpp](/home/iron/projects/Helixrkt/src/core.hpp:1) and [src/core.cpp](/home/iron/projects/Helixrkt/src/core.cpp:1), with [src/helix.cpp](/home/iron/projects/Helixrkt/src/helix.cpp:1) reduced to scheduler-specific logic

## [x] 3. Share typed map-field access across modules

[src/helix.cpp](/home/iron/projects/Helixrkt/src/helix.cpp:1) now has typed map-field helpers:

- `map_field_cell(...)`
- `map_field_map(...)`
- `map_field_vec(...)`
- `map_field_string(...)`
- `map_field_int(...)`

These are useful beyond the scheduler. Similar lookup/type-check patterns still exist elsewhere, especially in [src/builtins.cpp](/home/iron/projects/Helixrkt/src/builtins.cpp:1).

These helpers should move to shared runtime code so every module can use the same access pattern.

Why this matters:

- it removes repeated `find` / null / type-check sequences
- it standardizes map-field decoding
- it makes later frame and state schema changes cheaper

Success condition:

- typed field extraction is defined once
- builtins and scheduler both use the same helpers

Status:

- completed by moving the typed field-access helpers into [src/core.hpp](/home/iron/projects/Helixrkt/src/core.hpp:1) and [src/core.cpp](/home/iron/projects/Helixrkt/src/core.cpp:1), with both [src/helix.cpp](/home/iron/projects/Helixrkt/src/helix.cpp:1) and [src/builtins.cpp](/home/iron/projects/Helixrkt/src/builtins.cpp:1) consuming them

## [x] 4. Move list-frame arming into shared runtime code

`builtin_list(...)` still knows the internal scheduler frame schema:

- `"name"`
- `"values"`
- `"index"`

That is scheduler-owned structure, not builtin-owned structure.

The current frame-arming logic should become one runtime helper, conceptually something like:

- `arm_list_frame(vm, sequence_cell, start_index)`

Why this matters:

- the builtin should describe semantics, not frame layout
- the scheduler frame schema will likely evolve once `return` and richer signals exist
- any future resumable sequence form should reuse the same machinery

Success condition:

- `builtin_list(...)` stops constructing frame maps directly
- frame layout knowledge lives in one scheduler-facing helper

Status:

- completed via `arm_list_frame(...)` in [src/core.cpp](/home/iron/projects/Helixrkt/src/core.cpp:1), which now owns initial list-frame construction for the builtin layer

## [x] 5. Centralize terminal-status checks and terminal-result reads

Terminal-state knowledge is currently spread across:

- `is_terminal_status(...)`
- `attach_terminal_state(...)`
- `vm_result(...)`
- the `start` loop
- the internal `run_vm(...)` loop

This should become one shared protocol, conceptually along the lines of:

- `vm_is_terminal(vm)`
- `vm_terminal_result(vm)`

Why this matters:

- terminal-state semantics are part of the runtime contract
- repeated status-string checks increase drift risk
- scheduler loops should read more declaratively

Success condition:

- terminal checks are expressed through one shared interface
- loops no longer open-code status comparisons

Status:

- completed via shared helpers such as `vm_is_terminal(...)` and `vm_result(...)` in [src/core.cpp](/home/iron/projects/Helixrkt/src/core.cpp:1)

## [x] 6. Centralize status representation

The following status strings are still scattered across the runtime:

- `"ready"`
- `"running"`
- `"finished"`
- `"error"`
- `"signaled"`

This should be centralized, either as:

- shared string constants
- or a small enum with conversion helpers

The important requirement is that the representation be defined once.

Why this matters:

- status strings are part of the VM protocol
- repeated literal strings are easy to mistype
- signal and stepping work will keep expanding the status space

Success condition:

- status values are no longer hard-coded ad hoc throughout the codebase

Status:

- completed through `VmStatus` and `vm_status_name(...)` in [src/core.hpp](/home/iron/projects/Helixrkt/src/core.hpp:1) and [src/core.cpp](/home/iron/projects/Helixrkt/src/core.cpp:1)

## [x] 7. Refine child-VM stepping helpers

`start` and `step` are already closer than before, but they still repeat several scheduler-specific concerns:

- scheduler callback initialization checks
- child VM resolution and validation
- terminal-status inspection

After the earlier runtime helpers are moved out and shared, revisit these builtins and collapse them further.

Likely outcome:

- one helper to resolve and validate the child VM
- one helper to obtain terminal or current status
- `start` becomes a minimal loop over the same primitive used by `step`

Why this matters:

- these two builtins define the public scheduler surface
- they should read like two views on the same primitive

Success condition:

- `start` and `step` differ mainly in repetition count, not in protocol shape

Status:

- completed in [src/builtins.cpp](/home/iron/projects/Helixrkt/src/builtins.cpp:1), where `start` and `step` now share child resolution and the same primitive VM step operation, differing mainly by repetition

## [ ] 8. Revisit arithmetic helper reuse once more numeric builtins exist

`add` still contains a local pattern that future arithmetic builtins will likely reuse:

- evaluate left operand
- evaluate right operand
- propagate signals
- require integers
- construct result

Do not over-abstract this immediately. Wait until at least one or two more arithmetic builtins exist. Then extract a helper if the repetition becomes real rather than hypothetical.

Why this matters:

- this is a likely future reuse point
- but it should not be abstracted prematurely

Success condition:

- no action yet unless more arithmetic builtins are added

## [x] 9. Remove unused interface surface

Some functions still carry parameters that do not currently affect behavior. The clearest example is:

- `expect_map_cell(..., who)`

If `who` is not going to be used for diagnostic shaping, remove it. If it is going to be used, implement that usage consistently.

Why this matters:

- unnecessary parameters create false surface area
- dead interface features obscure the real contract

Success condition:

- either the parameter is meaningfully used, or it no longer exists

Status:

- completed for `expect_map_cell(...)`, which no longer carries the unused `who` parameter

## [ ] 10. Keep feature work subordinate to these refactors

The next features most likely to stress the runtime are:

- `return`
- richer signal behavior
- function boundaries
- more scheduler-visible control effects

Those features should not be layered on top of avoidable duplication in the current runtime core.

So the practical rule is:

- prefer completing items `1` through `6` before adding more control-flow machinery

That is especially important because item `1` is directly about signal propagation and should have been handled earlier.
