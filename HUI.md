# HUI Development Cockpit

## Mission

HUI is Helix's interactive terminal interface: an application UI, source editor,
and debugger operating on the same readable YAML that defines and persists a
Helix VM.

HUI does not render an abstract interface described by YAML. The literal YAML
text is the interface. HUI adds only terminal presentation and interaction:
syntax colors, selection, program-counter and breakpoint emphasis, cursor and
viewport movement, editing modes, and debugger commands.

The long-term interface must run directly on embedded targets over a TTY without
requiring Python or a Helix-aware host application.

The Python HUI demo is a deliberately disposable behavioral prototype. It
exists to settle literal-text navigation, key scope, prototype interrupt
context, and deterministic testing contracts before those contracts are
implemented in portable C++. It is neither the portability boundary nor a
second evaluator.

## Governing Direction

The canonical HUI implementation is a portable C++ frontend adjacent to, but
separate from, the runtime evaluator.

Development priorities, in order:

1. Preserve literal, readable YAML as the sole program and state display.
2. Keep evaluation semantics independent of terminals, colors, keys, and
   screen layout.
3. Use the runtime's existing cells, object paths, VM state, and YAML
   serialization rather than reconstructing Helix semantics in the frontend.
4. Run the same interaction model through desktop and embedded byte streams.
5. Make all presentation annotations display-only; do not write selection or
   program-counter decoration into the VM merely to render it.
6. Keep the interface keyboard-first, motionless, dense, and suitable for small
   terminal displays.

There must be no compiler-metadata-to-widget pipeline. Metadata such as
`keybinds`, help text, or preview text remains ordinary visible YAML attached to
an executable object. HUI may index that text and its object path, but it must
not transmute it into a separate menu or layout.

## Interaction Model

Given:

```yaml
main-menu:
  keybinds: [M, Home, <]
  help: Open the main menu
  main: [do-stuff]
```

HUI displays those exact YAML fields. Pressing a declared key selects or jumps
to the declaring VM's existing PC in the YAML text. The demo resolves
`keybinds` only on the active VM and its VM ancestors, nearest-first. It never
scans siblings, unrelated descendants, or the viewport. Invocation, stepping,
or editing then operates on that same literal document through the runtime.

The source author's YAML organization is therefore the interface organization.
Help, previews, executable forms, live values, and persisted VM state remain
inspectable and editable in place.

## Architectural Cut

```text
YAML <-> Cell graph <-> resolver/evaluator/builtins
            |
            | canonical YAML, object paths, VM status, debug operations
            v
     portable C++ HUI
     - text and path spans
     - cursor and selection
     - key decoding and modes
     - ANSI styling and viewport
            |
            | read bytes / write bytes / terminal dimensions
            v
   POSIX terminal | embedded UART/USB | deterministic test stream
```

### Runtime and evaluator

The existing C++ runtime owns semantic truth:

- YAML-to-Cell conversion and canonical YAML emission
- name and object-path resolution
- form evaluation and builtin behavior
- VM frames, status, results, signals, and breakpoints
- step, continue, invocation, reload, and resume semantics

The evaluator must not know about terminal dimensions, ANSI colors, cursor
coordinates, key escape sequences, panes, or editing modes.

### Portable C++ frontend

HUI owns interactive presentation:

- the displayed YAML text and its mapping from object paths to screen spans
- syntax styling and semantic display overlays
- current selection, cursor, viewport, focus, and modes
- decoding keys and resolving visible `keybinds`
- requesting runtime operations against selected object paths
- applying edits to text, validating through the canonical parser, and
  resuming only from valid state

The runtime must not depend on HUI. HUI depends on stable runtime operations and
data contracts.

### Platform byte streams

A byte-stream adapter is the narrow platform boundary used by HUI:

- read input bytes
- write output bytes
- obtain terminal dimensions when available
- enter and restore the platform's interactive terminal mode when applicable

On a POSIX workstation, this means standard input/output and `termios` raw-mode
handling. POSIX here refers to the Unix-like operating-system interface used by
Linux and macOS; it is not part of HUI's semantic model.

On an embedded target, the same bytes travel through UART or USB CDC serial.
The attached terminal sends ordinary key and escape-sequence bytes and
interprets ANSI output. It does not need to understand Helix.

A deterministic in-memory implementation must drive automated input and capture
rendered output without a real terminal.

### History and storage

Execution history is a separate capability from terminal interaction.

- Desktop workflows may retain Git-backed branching and snapshot navigation.
- Capable targets may provide bounded or persistent snapshot storage.
- Constrained targets may omit history without changing YAML presentation,
  selection, stepping, or editing semantics.

HUI should consume a history boundary rather than directly own Git behavior.
The existing Python debugger remains the behavioral reference for Git-backed
snapshots during migration.

## Presentation Invariants

- After removing ANSI control bytes, the program/state view is the canonical
  YAML text; HUI does not substitute widgets or reformatted summaries.
- Selection, current-PC, breakpoint, error, and change indicators are visual
  overlays associated with object paths, not persisted YAML comments.
- Styling never changes YAML content or runtime behavior.
- Rendering never sets a terminal background color; foreground colors, bold,
  and underline provide structure over the terminal's natural background.
  Reverse video is not used as an implicit substitute background.
- The same object path used by frames and breakpoints identifies the
  corresponding displayed text.
- Rendering is deterministic for a given YAML document, semantic overlay state,
  terminal size, and selection.
- Keys attached to executable objects navigate the literal source first.
  Execution remains an explicit runtime operation.
- Selection moves among semantic YAML nodes rather than arbitrary screen rows.
- The active PC comes from the active VM's first `state.frames` path, including
  its terminal sequence index, and falls back to that VM's `main` path.
- Selection, active PC, and the demo's prototype execution context are
  independent display state. None is serialized as a YAML annotation.
- Application keys are scoped to the active VM ancestry chain and resolve
  nearest-first.
- Run mode highlights the literal source declarations of keys that currently
  resolve; shadowed, unrelated, and write-mode bindings receive no command
  highlight.
- The interface uses no animation or transition.
- Screen organization follows the project's dense Gruvbox-dark operator-panel
  rules without obscuring or duplicating the YAML.

## Current Snapshot

The current frontend is `tui/helix_step.py`, an out-of-process Python debugger.
It:

- reads raw terminal keys with `termios`
- invokes the compiled runtime through wrapper YAML
- persists the stepped child VM back to YAML
- records execution snapshots in a per-target Git repository
- navigates time and branches through those snapshots
- renders the Git history in the terminal
- writes an end-of-line YAML comment to expose the current program counter in
  an external editor such as VS Code

This proves the serialized-state and branchable-debugging workflow, but it is
not the target architecture. Python currently owns terminal interaction, and
VS Code supplies text rendering, highlighting, cursor navigation, editing, and
file refresh. HUI will bring those responsibilities into the portable C++
frontend while eliminating VS Code as a required presentation layer.

`tui/hui_demo.py` is the current interaction prototype. It renders the ordered
canonical YAML directly, indexes mapping values and sequence items by semantic
path, keeps selection and PC as ANSI-only overlays, navigates by semantic
parent/child and document order, and delegates F10/F5 to the existing
out-of-process step/start boundary. F9 navigates to the previous Git-backed
debug snapshot through the same history boundary used by the existing Python
debugger. Its ordered nested-VM document demonstrates
nearest-first ancestor `keybinds`, excludes an unrelated sibling binding, and
models a key press as a display-only switch to the declaring VM's persisted PC.
The default launch copies that canonical document to `build/hui_demo.yaml`, so
runtime operations mutate only generated working state.

The demo intentionally does not establish evaluator interrupt semantics,
persist interrupt state, edit YAML, reimplement history storage, or replace the
planned C++ frontend.

## Active Checkpoint: Python Literal-YAML Demo MVP

Implemented and verified:

- ANSI-stripped YAML content is exactly the canonical ordered document
- selection and active PC are independent, non-persisted overlays
- the selected run-mode row alone receives a full-width background highlight;
  every other row retains the terminal's natural background
- YAML keys, scalars, literals, numbers, punctuation, strings, and comments
  have distinct renderer-only syntax colors that survive PC/selection overlays
- run-mode Down steps forward and Up restores the previous versioned state;
  Left/Right traverse semantic parent/first-child
- PgUp/PgDn clip a deterministic fixed-size viewport
- the deepest running VM in one unambiguous ancestry chain supplies the PC
- application keys resolve only along that VM chain, nearest-first
- F10 delegates one root step; F5 starts only the deepest running VM that owns
  the displayed PC, so continue ends at the current `run` block rather than
  advancing its running VM ancestors; both reload emitted YAML
- runtime values are merged into the original round-trip YAML tree so a step
  preserves existing order, comments, quotes, and flow/block collection style
- F9 checks out and reloads the previous versioned VM snapshot; at the initial
  snapshot it is a stable no-op, and a later forward action can reuse preserved
  future state
- appkey context switches are ephemeral overlays: every step, continue, or
  back operation clears them and derives the PC from the reloaded YAML, so an
  appkey can neither mask backward movement nor alter versioned history
- Up/F9 backward navigation is external version control over YAML text: it
  never calls Helix or depends on a runtime binary, and dirty saved edits are
  versioned target-only before the previous text is checked out
- forward runtime operations create snapshots only when the persisted YAML
  text changes; textually null steps are absent from program history
- the bundled `tests/assets/hui_core_demo.yaml` orbital-telemetry document is
  valid resumable Helix: its
  root/workspace/task frames form one running ancestry chain, F10 advances the
  nested computation, and continuation finishes with `doubled: 84` and
  `summary: 85`
- normal exit and failures restore the terminal boundary
- run mode maps Down/F10 to one forward step and Up/F9 to the previous
  versioned state; scoped appkeys and F5 apply only in run mode
- Escape toggles write mode, whose arrows, Home/End, printable input,
  Backspace/Delete, and Enter behave as literal text editing controls
- Escape validates and atomically saves a write buffer before returning to run
  mode; invalid YAML stays buffered and leaves the persisted VM unchanged
- `uv run python manage.py hui-test` runs scripted tests without a PTY or timing
- `uv run python manage.py hui-demo` is the canonical interactive launch

Hands-on use of this intentionally small prototype is the remaining evidence
needed before freezing these interaction contracts for C++.

## Next Checkpoint: Literal YAML C++ Vertical Slice

Build the smallest in-process desktop HUI that proves the durable ownership
boundary before adding editing or application keybind behavior.

Acceptance criteria:

- a C++ frontend displays the canonical YAML for a loaded VM
- the frontend reads keys and writes ANSI terminal output only through the
  platform byte-stream boundary
- the active frame's object path highlights the corresponding YAML text without
  modifying the YAML document
- selection and viewport movement operate on the displayed source
- step and continue delegate to existing runtime semantics
- no evaluation, resolution, frame, or serialization behavior is reimplemented
  in HUI
- deterministic automated tests use the in-memory byte stream
- HUI tests run through one obvious root-level `manage.py` command backed by
  `scripts/operations.py`
- existing runtime and HCC fixtures continue to pass

The first vertical slice targets the POSIX adapter because it is the fastest
place to verify behavior. Its frontend logic and tests must not depend on
POSIX-specific APIs, so an embedded adapter can reuse them unchanged.

## Roadmap

### 0. Prove interaction semantics in Python

- exercise literal ordered YAML rendering with display-only overlays
- test semantic node navigation and fixed viewport behavior
- test active-PC discovery and VM-ancestor-only application key scope
- keep prototype interrupt context isolated from persisted runtime semantics
- capture every behavior through scripted input and fixed dimensions

### 1. Establish the portable boundary

- expose the minimum existing runtime operations needed by an in-process
  debugger without moving terminal concerns into the runtime
- define the byte-stream and terminal-dimension boundary
- add the deterministic in-memory adapter and canonical root-level HUI test
  workflow
- implement and verify the POSIX adapter

### 2. Present and navigate literal YAML

- render canonical YAML with path-to-text-span correspondence
- apply syntax and semantic styling without altering content
- add cursor, selection, viewport, and current-PC presentation
- reproduce step, continue, and breakpoint inspection through runtime calls

### 3. Make source objects directly interactive

- discover visible `keybinds` metadata without generating widgets
- navigate from keys to executable object paths and their literal YAML
- define and test key normalization, scope, and conflict behavior
- add explicit invocation from the selected object
- present help and preview metadata as emphasized literal source

### 4. Add live editing

- add motionless normal/insert-style editing modes
- pause execution while text is edited
- parse and validate edits through the canonical YAML/runtime path
- preserve resumable VM state where the edited object graph remains compatible
- report invalid edits in place without corrupting the last valid VM
- connect edits with branchable snapshots on platforms that provide history

### 5. Run on embedded targets

- implement UART or USB CDC byte-stream integration
- establish explicit flash, RAM, and terminal-size budgets per target
- verify operation without Python, Git, or a Helix-aware host
- select bounded history behavior independently from core HUI interaction

### 6. Retire overlapping frontend behavior

- compare the C++ vertical slice against the Python debugger's verified
  stepping and history behavior
- remove Python interaction code only after its replacement is covered by the
  canonical HUI workflow
- retain Python only where it remains useful for host automation, fixture
  orchestration, or optional Git-backed tooling

## Verification Contract

HUI testing is routinized through the root-level Typer/Rich `manage.py` command
and implemented in `scripts/operations.py`. Tests must not depend on a
human terminal, VS Code, timing, or ad-hoc shell scripts.

The current prototype suite is `uv run python manage.py hui-test`, backed by
`tui/tests`. It drives exact key strings and terminal dimensions through an
in-memory terminal. Desktop/in-memory byte-stream parity remains an acceptance
criterion for the portable C++ checkpoint.

Each HUI behavior should be verified across the applicable dimensions:

1. exact visible text after ANSI control bytes are removed
2. deterministic styles and cursor placement
3. selected and active object paths
4. runtime state after requested operations
5. identical frontend behavior through desktop and in-memory byte streams

Run the narrowest HUI suite during development, then the complete HUI and
runtime suites before each logical checkpoint. Run HCC fixtures whenever shared
source-location, serialization, or debugging contracts change.

## Cockpit Maintenance

Update this file at every verified HUI checkpoint:

- advance the active checkpoint and its acceptance criteria
- record implemented and verified behavior in the current snapshot
- preserve the runtime/frontend/platform ownership boundary
- update embedded resource constraints as real targets are measured
- remove migration work only after the canonical replacement is verified

Keep detailed implementation history in Git commits. Keep this cockpit current
and sufficient to recover HUI's motivation, architecture, state, and immediate
work after context clearing.

## Decision Log

- 2026-07-26: Defined HUI as an interactive view of literal YAML rather than a
  UI generated from YAML metadata.
- 2026-07-26: Selected a portable C++ frontend so the canonical interface can
  run directly on embedded targets without Python.
- 2026-07-26: Kept terminal presentation outside the evaluator and placed
  desktop POSIX, embedded serial, and deterministic test streams behind one
  byte-oriented platform boundary.
- 2026-07-26: Kept Git and snapshot storage separate from core HUI interaction
  so constrained targets can retain the same interface without hosting a
  repository.
- 2026-07-26: Added a Python interaction prototype to validate semantic YAML
  navigation before committing the behavior to the portable C++ frontend.
- 2026-07-26: Defined the active PC as the first persisted frame path, with
  `main` as the unframed fallback, independently from semantic selection.
- 2026-07-26: Scoped application keys to the active VM and its VM ancestors,
  nearest-first; prototype context switching remains display-only.
