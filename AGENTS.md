# Helix Conceptual Map

## Purpose

Helix is an experimental C++ runtime in which readable YAML is both program source and persisted VM state. Programs are object graphs, vector forms are executable expressions, and completed or suspended execution is serialized back to YAML. HCC is the companion Python compiler that lowers a small C subset into readable Helix YAML.

## System Flow

```text
C source -> pycparser AST -> hcc/compiler.py -> Helix YAML
                                               |
YAML file -> ryml_interface -> Cell graph -> resolver/evaluator -> builtins
                                               |                    |
                                               +---- VM state <-----+
                                                        |
                                             YAML on stdout / TUI snapshots
```

The runtime starts at the root mapping's `main`. A vector such as `[add, x, 1]` resolves its first element as the actor and evaluates through the builtin zygote. The top-level mapping is a `VmCell`; nested mappings containing `main` also become `VmCell`s, while other mappings become `ScopeCell`s.

## Ownership Map

| Area | Responsibility |
| --- | --- |
| `src/core.hpp`, `src/core.cpp` | Cell hierarchy, parent links, lookup and object paths, structured errors, typed slots, VM state helpers |
| `src/helix.cpp` | Name resolution, form evaluation, frames, breakpoints, VM lifecycle, executable entrypoint |
| `src/builtins.cpp` | Zygote builtin installation, arithmetic/data/control forms, functions, child `start`/`step` |
| `src/ryml_interface.*` | YAML-to-Cell conversion, source locations, `include`, `name:type` sugar, YAML emission, native modules |
| `scripts/operations.py` | Canonical build, analysis, fixture discovery, runtime/HCC test harnesses |
| `manage.py` | Canonical Typer/Rich repository command surface; delegates policy to `scripts/operations.py` |
| `hcc/build.py` | Compatibility HCC entrypoint; not a canonical workflow surface |
| `hcc/compiler.py` | `pycparser` C AST lowering to human-readable Helix forms |
| `HCC_Plan.md` | HCC development cockpit: mission, debugging invariants, current checkpoint, roadmap, and verification state |
| `HUI.md` | Python interaction checkpoint and canonical direction for the literal-YAML C++ terminal interface |
| `tests/`, `hcc/tests/` | Executable YAML specifications for runtime behavior and C lowering |
| `tui/helix_step.py` | Out-of-process stepping and branchable per-target Git snapshots |
| `tui/hui_demo.py`, `tui/tests/` | Literal-YAML interaction prototype and deterministic scripted tests |
| `lib/sfml/` | Optional `.so` module; it must remain isolated from the core runtime binary |
| `webdemo/` | HTTP substrate for stateless one-turn Helix message-handler functions |
| `site/`, `.github/workflows/pages.yml` | Static public project page, published to GitHub Pages from `main`; use the canonical `flintwinters/helix` repository name in public URLs |

`ryml/`, `build/`, `debug_*`, caches, and local lock/environment files are ignored dependencies or generated state, not primary project source.

## Runtime Invariants

- Parent links define lexical/object resolution. Attach children through `MapCell::set()` and `VecCell::append()`; clear links deliberately when detaching trees.
- Persist resumable execution as root-relative object paths in `state.frames`, never as opaque pointers. VM statuses are `ready`, `running`, `finished`, `error`, or `signaled`.
- Signals are control flow. Propagate return/error signals immediately and materialize terminal state consistently under `state` (`status`, `frames`, `result`, and `error` when applicable).
- YAML shape is a public behavioral contract. Preserve source locations, structured error details, include semantics, and round-trippable state.
- Builtin argument vectors include the actor at index zero; use the shared arity/type/error helpers rather than open-coding validation.
- Native communication modules invoke Helix handlers through `invoke_message_handler`; one call consumes one message and returns one response without module-owned handler state.
- `run` arms a resumable vector sequence. `list` returns the sorted, deduplicated binding names reachable through the same lexical receiver chain as ordinary lookup.
- Dotted lookup, function definition scope, nested VMs, breakpoints, and typed assignments depend on parent topology. Clone only where ownership/isolation requires it.
- Typed field sugar (`x:i32: 5`) becomes `{type: i32, value: 5}`. Assignment validation currently supports `i32` and `i64`.

## Canonical Workflows

```bash
uv run python manage.py build           # build build/helix
uv run python manage.py cpp-test        # build and run tests/*.yaml
uv run python manage.py cpp-test --optimize-size  # verify production-size runtime
uv run python manage.py hcc-test        # build and run hcc/tests/*.yaml
uv run python manage.py                 # build, tidy, runtime fixtures, LOC, size
./build/helix path/to/program.yaml       # execute one Helix program
uv run python -m hcc input.c -o out.yaml
uv run python tui/helix_step.py program.yaml
uv run python tui/hui_demo.py             # launch the ordered nested-VM demo
uv run python manage.py hui-demo         # canonical interactive demo launch
uv run python manage.py hui-test         # deterministic HUI demo tests
uv run python manage.py webdemo          # build the Helix HTTP demo module
uv run python manage.py webdemo-test     # verify Helix message-handler turns over HTTP
uv run python manage.py site-check       # check project-page links, assets, and SEO metadata
```

The project requires Python 3.13+, C++20 `g++`, CMake, and the ryml source tree
at `ryml/`; canonical builds configure and compile the selected ryml profile
under `build/dependencies/`. Useful workflow flags include `--fail-fast`,
`--valgrind`, `--no-dynamic-libraries`, `--no-cpp-linenums`, `--optimize-size`,
and `--lib`.
`--optimize-size` is the production/microcontroller profile: it implies `-Os`,
LTO, and `-fno-rtti`; rebuilds static ryml as `MinSizeRel` with LTO, RTTI
disabled, and short diagnostics; strips the executable; disables C++
error-origin metadata; and disables dynamic library loading and symbol exports.
It is therefore incompatible with `--lib`. Normal and size-optimized ryml
artifacts use isolated project-local build directories so their cached CMake
profiles cannot contaminate one another.

Runtime fixtures contain exactly one of `program` or `source`; non-smoke fixtures provide `expected`, with optional stdout assertions. HCC fixtures provide `c_source` and normally assert both emitted `expected` YAML and `expected_state`. Add or tighten the nearest fixture whenever semantics change.
Raw YAML programs used by source-backed runtime fixtures belong under `tests/assets/`; fixture discovery deliberately excludes every `assets` subtree.

## HUI Demo Invariants

- Canonical ordered YAML remains the displayed content; selection and PC
  styling and syntax highlighting are ANSI-only overlays and are never
  persisted.
- Every visible YAML row has a right-aligned line-number gutter in run and
  write modes; the gutter is presentation-only and write cursors remain
  aligned with YAML columns.
- HUI presentation uses a localized full-width selection highlight and a
  lighter full-width highlight on suspended stack-frame rows in the active VM
  ancestry. Other rows retain the terminal's natural background. Title,
  footer, PC, syntax, and mode distinctions otherwise use foreground color and
  text attributes; reverse video remains excluded.
- A dedicated top row identifies the active YAML document by file path so
  generated debug copies and explicitly opened targets are unambiguous;
  project-local paths use a compact explicit `./` prefix. Runtime status uses
  the separate row immediately below it.
- The first scalar in every flow array uses a non-bold yellow foreground,
  distinguishing the actor position of executable vectors.
- Navigation targets semantic mapping values and sequence items identified by
  object paths, independently from viewport rows.
- Escape toggles run/write modes. In run mode Down steps forward and Up checks
  out the previous versioned state; runtime controls and scoped appkeys apply
  only in run mode. Write mode is a literal text editor with cursor movement,
  insertion, deletion, line splitting/joining, word-wise Ctrl+Left/Right and
  Ctrl+Backspace/Delete, document-boundary Ctrl+Up/Down/Home/End, and atomic
  validated saves.
- The active PC is the deepest running VM's first frame path, or its `main`
  path when unframed. The demo fixture guarantees one running VM ancestry
  chain.
- Application keys resolve only from the active VM through its VM ancestors,
  nearest-first. A mapping form binds each lowercase key to a distinct relative
  semantic path; legacy scalar/list forms still switch to the declaring VM's
  PC. Siblings, unrelated descendants, and viewport proximity are irrelevant.
- In run mode, named VMs and key declarations are underlined only when their
  application keys are currently dispatchable from the same nearest-first
  resolution result. Shadowed and out-of-scope declarations, and every overlay
  in write mode, remain unmarked. Underlines preserve the YAML syntax color.
- Prototype interrupts only switch displayed execution context to the
  declaring VM's existing PC; they do not define evaluator interrupt semantics.
  This context is cleared before every step, continue, or back action so the
  reloaded YAML snapshot is always the sole source of history and PC truth.
- The bundled HUI document is executable Helix state, not presentation-only
  sample data. Its root frame begins at the first root action and descendants
  remain ready until explicit stepping enters them; the state is verified by a
  source-backed runtime fixture.
- Interactive HUI execution uses the existing per-target Git-backed debug copy:
  F10 and F5 commit textually changed forward states, while F9 checks out the
  previous snapshot. A runtime operation that leaves persisted YAML unchanged
  creates no history entry. Syntax and PC presentation remain absent from
  persisted YAML.
- F5 continues only the deepest running VM that owns the displayed PC. Its
  `start` operation completes the current `run` block without advancing the
  running ancestor VMs; F10 remains a root-level single step.
- Finished, errored, and signaled root VMs reject step and continue before the
  runtime or snapshot graph is touched. Backward YAML history remains available.
- Up/F9 backward navigation is purely external YAML version control. It never
  invokes Helix, requires no runtime binary, and preserves dirty edited text as
  a target-scoped version before checking out the previous snapshot. Unrelated
  files in the debug directory are never staged. Only the absence of an earlier
  recorded text at history root prevents further movement.
- HUI runtime reloads merge values into the original round-trip YAML tree.
  Existing mapping order, comments, scalar quotes, and flow/block collection
  choices are literal source structure and must survive stepping.
- HUI tests use scripted input and fixed terminal dimensions through
  `uv run python manage.py hui-test`, without a PTY, timing, or manual steps.
- A default no-target launch creates a unique project-local working copy, so
  an earlier session's debug repository can never determine the initial PC.

## Change Discipline

- Formulate behavior as explicit invariants and inspect declarations before naming or changing anything; never guess symbols.
- Reuse and extend existing helpers. Centralize repeated behavior and avoid expedient compatibility paths or parallel implementations.
- Keep production code and tests in the same logical checkpoint. Run the narrowest relevant suite, then the broader suite when shared runtime behavior changes.
- Do not stop merely because the tree is dirty. Preserve intent, stage explicit paths only—never `git add .` or `git add -A`—and commit every verified logical checkpoint with a detailed message.
- Update this map when architectural ownership, invariants, workflows, or project state materially changes.
- For UI work: use a dense high-contrast gruvbox dark presentation, minimal labels/spacing/borders, and no animations or transitions.

## Current Boundaries

- HCC exists to apply Helix's debugging capabilities to C programs; readable output serves debugging fidelity rather than being the final objective. `HCC_Plan.md` is the canonical cockpit for HCC priorities and state. The current lowerer handles integers, locals, functions/calls, returns, `if`, `while`, and arithmetic. C source provenance is the next architectural checkpoint; comparisons, pointers, aggregates, allocation, and libc remain deferred.
- The legacy Python debugger invokes the compiled runtime through temporary wrapper YAML and stores snapshots in a per-target debug repository. The Python literal-YAML HUI demo is the current behavioral prototype for semantic navigation, PC overlays, scoped keys, and debugger controls. Neither is the target architecture; `HUI.md` defines the portable C++ replacement that keeps terminal behavior outside the evaluator and runs through POSIX or embedded serial byte streams.
- Verification on 2026-08-13: the stateless HTTP message-handler workflow passes its structured, raw, empty, and malformed-body turns. Runtime fixtures pass 52/52 in both normal and production-size profiles, and HCC fixtures pass 3/3. HUI demo and workflow tests were last verified at 54/54 on 2026-08-01.
