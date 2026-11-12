# Helix Charter Cockpit

**Goal:** Helix is a language I can reach for every day, for real scripts and automations, without apologizing for it.

The test for "daily driver" is simple: I start a task in Helix instead of Python or shell, I finish it, and I can open the saved state afterwards and understand exactly what happened.

How to use this file: tick a box only when the feature works end to end *and* has a golden case in `tests/cases/` (run `./manage.py test`). A feature that only works in a demo stays unticked. Edit freely; this file is for humans, while `AGENTS.md` holds the agent-facing architecture and invariants.

Audit (2026-11-12): a parent stays open while any of its stated obligations remain
open. Implementation evidence is retained even where acceptance is incomplete.
In particular, section 11's inventory, provider, composition and command-lifecycle
implementations have dedicated automated suites, but lack golden cases for those
contracts in `tests/cases/`; their boxes remain open under the rule above. A
`tests/programs/` example or a generated suite fixture is not a golden case.

**Where it is headed:** the niche Nix and Ansible occupy, declarative machine configuration, approached through types. A machine config is a type, fully resolved at type time and inheriting from abstract machine type classes; section 8 holds that road. Daily-driver scripting comes first because the config language has to be a language worth using.

**Where we are:** a resource-bounded expression language with a pure core, first-class functions, declarative types, annotations, class composition and explicit type-time evaluation. Caller-authorized host effects, in-memory interruption, file imports and pure error recovery work; disk suspend/resume and declarative machine application remain ahead.

---

## 0. Foundation — complete

YAML representation graphs retain parent links, source spans and root-relative paths. Explicit `$call` and `$ref` forms use lexical lookup; `$literal` protects arbitrary data. Scalar style and mapping presentation order do not affect execution; execution detects cycles and emits structured diagnostics. Finished ordinary-data roots round-trip through YAML. `./manage.py` owns golden testing and structural linting through the hyperlinter submodule.

## 1. Core language — complete

- Values: strings with concatenation/length/slicing, integers, floats, booleans, null, lists and maps; explicit type rules without silent coercion (`LANGUAGE.md`).
- Operators: checked integer arithmetic, float math, variadic `add`/`mul`, truncating `div`/`mod`, comparisons and short-circuit boolean operations.
- Control: lazy `if`/`cond`, `let`, collection iteration and bounded looping without recursive calls.
- Functions: YAML declarations, parameters/results, lexical closures and recursion with a clean depth-limit error; evaluation order and laziness are documented.

---

## 2. The thing that makes Helix Helix: state you can read and resume

If these are not great, there is no reason to use Helix over anything else.

Reversibility is a promise type. A machine that mixes in this promise must expose all of its behavior and complete execution state so it can always be serialized and restored. This includes stack, heap, local bindings, continuations, iteration progress and pending effects wherever they determine execution; hidden host state cannot carry part of the continuation. Suspend/resume follows from satisfying this promise. The runtime representation must serve that contract; its concrete layout and the promise syntax remain undecided.

- [ ] A reversibility promise type enforces complete behavioral and state exposure on machines that mix it in
- [ ] Serialization captures the whole promised machine, including nested calls and live allocations, without dependence on the original process
- [ ] **Suspend/resume:** a run can pause, save to YAML, and continue later from that file
- [ ] Execution position persisted as root-relative paths, never opaque pointers
- [ ] All five statuses actually occur and round-trip: `ready`, `running`, `finished`, `error`, `signaled`
- [ ] Hand-edit a suspended state file, resume it, and the edit takes effect
- [ ] Deterministic replay: same state in, same state out
- [ ] Source locations preserved on evaluated values, not only on errors
- [ ] State diffs read well in `git diff` (stable key order, minimal churn)
- [ ] Versioned state format with a clear migration story
- [x] YAML representation boundaries decided: bounded acyclic aliases and standard tags are supported; cyclic aliases, custom tags and non-string keys are rejected (`yaml_model` golden and conformance suite).

---

## 3. Talking to the world

Completed: caller-authorized file I/O, arguments/exit codes, environment, standard streams, command capture, HTTP, time/randomness and JSON/YAML/CSV codecs. Pending effects are explicit in machine state; host events can interrupt and resume in-memory execution.

Evidence: `effects_world` is a golden pending-request case and an end-to-end
`effects` suite program that writes/reads a file, consumes arguments/environment/
stdin, emits stdout, captures a command, calls a local HTTP server and chooses an
exit code. The suite checks capability denial, import confinement, symlink
escapes, HTTP redirects, host failures and exact seeded randomness. `std_codecs`
and the existing YAML golden cases cover data interchange; `semantics` compares
JSON/CSV with independent Python libraries. `effects_pending` preserves the
request and original machine while proving type-time rejection. `execution`
tests interrupt/resume, pending requests, cleanup and no repeat after completion.

Scope: file/stream adapters use UTF-8 text; HTTP currently means GET through
installed `curl`. Signals are explicit host event APIs, not OS signal handlers.
Suspension and interruption preserve live continuations in memory; emitted
inspection state is not a restorable disk snapshot. Retained branches require
explicit host handling and do not share a global effect deduplication service.
These limits leave section 2's restoration and replay obligations open.

---

## 4. Errors I can actually debug — complete

Structured errors are readable data with paths, spans, call chains and retained causes. `try`, kind-matched recovery, `raise`, assertions and `finally` support independent validation and cleanup on either outcome. Caught errors remain visible in saved roots. Malformed-input mutation sweeps check structured failures rather than panics.

Pure recovery checkpoints preserve completed root fields and restart unfinished calls after correction. Full continuation restoration remains in section 2.

## 5. Reuse — complete

File imports provide isolated namespaces, documented visibility and circular-import diagnostics. The standard library has a documented core. Dependencies use repository-pinned vendored paths with provenance/update guidance in `LANGUAGE.md`; `import_vendored` and the `imports` suite cover relocation and version isolation.

---

## 6. Daily tooling

Completed: `run` and exit codes, REPL, canonical formatter, non-executing `check`, step/trace, saved-state inspection, content-based watch, JSON Schema/LSP, shell completion/help and `cargo install`. `std:test` supplies in-language assertions, semantic state comparison and golden-test decisions over captured text; file discovery and process supervision remain host duties.

Evidence: `daily_interactive`, `daily_inspection` and `tooling_source` golden
cases accompany the `interactive`, `inspection` and `static` suites. The CLI
suite verifies run exit codes and project wrappers; installation is exercised
with `cargo install --path . --locked` into a project-local prefix and execution
outside the checkout. Completion covers bash, zsh and fish. The LSP provides
full-document synchronization, static diagnostics and formatting, alongside a
structural JSON Schema.

Plain `check` parses and checks known builtin arities without evaluating or
opening imports; dynamic calls and value-dependent type failures remain runtime
checks under `check --evaluate`. Step/trace expose engine transitions; inspection
reads ordinary saved states and inspection snapshots, not restorable disk
continuations. Watch follows transitive imports by content. These boundaries
leave section 2's complete suspend/resume promise unchanged.

---

## 7. Trust

- [x] Performance: a 10k-node program runs in well under a second
- [x] Memory stays bounded on long runs: shared work/data budgets and value/graph/depth caps; `memory` checks constant-state iteration and scaling
- [x] Fuzzing of the parser and evaluator
- [x] Sandbox/permission model for current effects: caller-authorized import/file roots, environment names, executables and HTTP origins, plus stream/clock/random grants; transitive and symlink-escape checks. These are capability boundaries, not OS isolation.
- [x] Stability promise: documented what may change and what is frozen
- [x] Language reference (started: `LANGUAGE.md`) and a tutorial that a stranger could follow
- [ ] A changelog — no project changelog is present; Git history alone does not fulfill this item

### Testing depth and breadth

The current-runtime testing infrastructure is implemented; this does not complete every trust/documentation obligation above. The coverage map and limits are in `tests/README.md`; suites and their contracts are discoverable with `./manage.py test --describe`. Actor scenarios are measured in `runtime/coverage/actors.json`, peak RSS and timing in `runtime/memory/report.json`, and compiled mutation sensitivity in `runtime/mutants/report.json`. Scenario counts are not compiler branch-coverage percentages or proofs of exhaustive correctness.

Treat testing as a substantial engineering responsibility. The Rust/Python size ratio is a prompt to inspect gaps, not a coverage metric or a target to pad toward. Build on the existing golden, determinism, round-trip and damaged-input checks; every new suite must be reproducible through the root `./manage.py` entrypoint, with Python orchestration and fixtures kept inside the project. Completion requires automated checks demonstrating the claimed contract; golden cases remain required for language features.

Completed coverage spans:

- Builtins, forms and feature interactions; arity/types, empty inputs, numeric boundaries, Unicode and precise blame.
- Parser/source graphs, imports/module isolation, ordinary-data fixpoints and function/machine inspection contracts.
- Generated properties, metamorphic laws, independent reference oracles, reproducible fuzzing and deliberate implementation mutations.
- CLI subprocesses and public VM APIs; resource boundaries, stack safety, performance/memory scaling and type/class algebra.
- Harness discovery, comparison, timeout/crash detection and actionable failure artifacts; fast regressions plus scheduled bounded generated/fuzz/scale runs.

### Obligations activated by later milestones

These require features from sections 2–3 and a versioned state format. They remain explicit obligations, not claims that disk restoration, effect replay or state migration already works. Today's immutable in-memory pause points, pure recovery and ordinary-data finished-root fixpoints are covered separately; named function and machine inspection graphs are not executable disk snapshots.

- [ ] When suspension lands, compare uninterrupted execution with save/resume at every supported boundary, including nested calls, live locals, hand-edited states and fresh-process restoration
- [ ] Extend the existing controlled-adapter failure, retry, cleanup, permission and in-memory interruption tests to fresh-process disk resume; prove completed effects are not accidentally repeated after restoration
- [ ] Keep compatibility fixtures for persisted states and public errors; test supported upgrades and explicit rejection of unsupported versions as versioning lands

---

## 8. Types: declarative, first-class, and the machine-config niche

The long-range target. A type is ordinary readable YAML, a value like any other, and a machine config is a type rather than a script: everything about it is known and checked at type time, before any effect touches a machine.

Principles that hold for the whole section, and that settle most design questions inside it:

- **One language, one graph.** Types live in the same object graph as programs and state, are written in the same YAML, and are computed by the same evaluator. There is no schema dialect, template layer or generics syntax beside the language. If a feature needs a second notation, the design is wrong.
- **The round trip covers types.** The founding promise (source and saved state are the same readable thing) applies to types unchanged: a type can be read, diffed, hand-edited and re-read, and a saved state shows the types that governed it.
- **A type describes, it does not act.** A type says what a value is. It never converts, coerces, fetches or repairs; checking a value against a type either passes or yields error data. This matches the no-silent-coercion rule in `LANGUAGE.md`.
- **Wrong is loud and early.** The reason for types here is to move failures from "halfway through changing a machine" to "before anything ran", and to report all of them at once, each blamed on a node with path and line.
- **Explain where things came from.** In Nix and Ansible the hard question is rarely "what is this value" but "who set it and why did it win". Helix answers that from the state, not from a debugger or a precedence table held in the reader's head.

The sketch below shows the intended feel only; the syntax is undecided (see Open questions) and must not be treated as a spec.

```yaml
has_ssh: {$call: [class, {ssh_port: {type: port, default: 22}}]}
server: {$call: [class, {hostname: string, packages: {$call: [list_of, {$ref: string}]}}]}
web01: {$call: [inherit, {$call: [server, {$ref: has_ssh}]}, {hostname: web01, packages: [nginx]}]}
```

### Declarative types

Audit evidence: `types_declarative`, `types_annotations`, `types_are_values`, `classes`, `class_conflicts`, `class_constraints` and `class_parametric` cover the completed declaration, annotation and class subitems below. The `golden` suite also checks emitted-root fixpoints. Broader parent boxes remain open where their diagnostic, provenance or serialization promises exceed these contracts.

A type should read like the value it describes with the specifics left open: someone who has never seen Helix can look at a record type and say what a valid value looks like. Declarations are order-independent facts, not a checking procedure, so reordering fields or splitting a declaration across maps changes nothing. Types are opt-in descriptions layered on the existing untyped core, never a reason existing programs stop running.

- [ ] A type is declared as plain YAML data in the object graph (fields, their types, defaults), not in a separate schema language
  - [x] A declaration is an ordinary source node: it has a path and span, and is found by the same lexical lookup as any value (`types_declarative`, `types_first_class_errors`; retaining locations on evaluated values remains separate)
  - [x] Declarations are order-independent: reordering record fields changes neither the type nor what it accepts (`types_derivation.round_trip` reconstructs fields in canonical order; the shared checker consumes the resulting field map)
  - [x] A type may be named in a map or written inline where it is used, with the same meaning
  - [ ] A malformed declaration is a structured error blaming the declaration node, not the value later checked against it
- [x] Scalar, list, map and record types, plus optional fields, unions, enums and literal types
  - [x] Scalar types (`integer`, `float`, `string`, `boolean`, `null`) and a top type that accepts anything
  - [x] Homogeneous list and map types over an element type
  - [x] Record types: named fields, each with its own type
    - [x] Required fields: absence is a violation
    - [x] Optional fields: may be absent, checked when present
    - [x] Defaulted fields: the default is itself checked against the field's type
    - [x] Decided and documented: records are closed; there is currently no open-record constructor (`LANGUAGE.md`, `types_declarative.fits.endpoint_open_field`; row constraints remain future algebra work)
  - [x] Unions of types
  - [x] Enums and single-value literal types
  - [x] Types nest within the runtime's documented resource/depth bounds (lists, records and unions; `types_declarative`, `type_algebra`); unbounded depth is not promised
- [ ] Refinements: a type can carry a predicate (`port` is an int in 1..65535), written as an ordinary Helix expression
  - [x] The predicate is a named or inline `fn` from the value to a boolean, run only after the base type passes (`types_declarative`, `types_first_class`; the shared checker gates predicates on base membership)
  - [ ] A predicate that fails or returns a non-boolean is an error blamed on the predicate, distinct from "value rejected"
  - [ ] A rejected value's violation names the refinement, so the report says which rule failed and not only that one did
  - [ ] Refinements stack (a refinement of a refinement) and compose with every other type former
- [ ] Annotate a value or a `fn` parameter/result with a type; a mismatch is a structured `type` error blaming the offending node with path and line
  - [x] Annotate a value in a map; the annotated value is still read by other expressions as the plain value
  - [x] Annotate `fn` parameters; a bad argument blames the argument at the call site
  - [x] Annotate a `fn` result; a bad result blames the body, with the call recorded
  - [ ] Unannotated code runs exactly as before: every pre-types golden case is unchanged
  - [ ] The error carries expected type, actual value and the path *inside* the value where they diverge
- [ ] Type checking collects every violation in one pass instead of stopping at the first
  - [ ] A violation is data: path into the value, expected, actual, and the rule that rejected it
  - [x] All violations of one value against one type are returned together, nested ones included
  - [ ] Violations come out in a stable, documented order so reports diff cleanly
  - [ ] All annotation failures of a whole program are reported in one run
- [ ] Types survive in the saved state and round-trip: emitted `root` re-read yields the same types
  - [ ] A type emits as the declaration that was written, not an expanded or internal form
  - [x] Re-running an emitted ordinary-data `root` under `$literal` is a data fixpoint, including data-only type descriptions (covered by the existing round-trip check; types containing predicate functions use non-restorable inspection graphs)
  - [ ] Annotations survive too: the saved state shows which type governed which value
- [ ] `helix check` type-checks a program without running it or performing effects
  - [ ] Checks every annotation whose value is computable at type time and reports the rest as unchecked, never as passed
  - [ ] Output is the usual state document (`status` plus violations), with the usual exit codes
  - [ ] Covered by the mutation sweep: damaged programs yield a well-formed state, never a panic

### First-class types

"First-class" means there is no type-level language: whatever can be done with a number or a map can be done with a type, using the functions, `let`, iteration and errors that already exist. Abstraction over types is therefore just a function that returns a type, which is how Helix avoids the templating (Jinja in Ansible) and the bespoke module machinery other tools grow. Type time is not a different interpreter; it is ordinary evaluation run under a stricter contract (pure, no effects, bounded), and that contract is what makes a type trustworthy to compute before a machine is involved. Functions are first-class values, with referenced lexical bindings preserved in readable closures.

- [x] Types are values: bind them with `let`, pass them to and return them from functions, store them in lists and maps
  - [x] Scalar and collection type names
  - [x] Declared types (records, unions, refinements and the rest)
  - [x] Equality on types is defined and documented (`eq` of two types)
  - [x] Functions are first-class values, so a refinement predicate or a type constructor can be stored and passed like a type
- [x] Type constructors are ordinary functions from types (and values) to types (`{$call: [list_of, {$ref: t}]}`, `{$call: [with_field, {$ref: t}, {$ref: name}, {$ref: u}]}`)
  - [x] Built-in formers accept computed types as arguments, not only literal names
  - [x] A user `fn` that returns a type is usable anywhere a type is expected
  - [x] Derive a type from another: add, drop or retype a field; make every field optional
  - [x] Build a type by iteration (`map`/`fold` over a list of field names)
  - [x] A constructor given a non-type argument fails with a `type` error blaming that argument
- [ ] Reflection (`types_reflection`, `types_first_class` golden cases): read a type's fields, defaults and parents as data; ask `{$call: [is, {$ref: value}, {$ref: t}]}` and `{$call: [subtype, {$ref: a}, {$ref: b}]}`
  - [ ] `{$call: [is, {$ref: value}, {$ref: t}]}`
    - [x] For scalar and collection type names and `any`; an unknown name is a `type` error
    - [ ] For every declared type — structural records and refinements work, but class membership parses only field declarations (`src/builtins/typing/ty.rs`); fixed values and whole-record constraints are enforced by class realization, not by `is`
  - [x] Read a record type's fields, each field's type, and its defaults as plain lists and maps
  - [x] Read a class's parents and its still-open fields
  - [x] `{$call: [subtype, {$ref: a}, {$ref: b}]}`, with documented rules per type former
  - [ ] A value built by reflection re-reads as the same type (reflection and declaration are inverse) — record-field reconstruction works, but `fields` omits class parents, fixed values, constraints and provenance; predicate functions do not restore from emitted YAML
- [ ] Type-time evaluation (`types_type_time`, `types_type_time_effect`; `type_time` suite): a well-defined phase that runs pure Helix to compute types, with a guarantee it performs no effects and terminates or fails cleanly
  - [x] Decided and documented: what marks type time (see Open questions)
  - [x] An effect attempted at type time is a structured error naming the effect and the node
  - [x] Bounded: the existing `depth` and `limit` caps apply, so type time ends or fails cleanly
  - [ ] Deterministic: no time, randomness, environment or file contents reach type time unpinned — effect requests and file imports are blocked, but already supplied function arguments have no input-pinning/provenance enforcement
  - [x] The same type-time result whether reached by `helix check --evaluate` or by a full run (`type_time` suite); plain `check` does not evaluate type-time regions
- [ ] Type-time errors (`types_type_time_error` golden case) are the same structured errors as run-time ones, with the phase recorded in the state
  - [x] Same shape and kinds (`kind`, `message`, `path`, `line`, `column`), catchable with `try` like any other
  - [x] The saved state records which phase failed
  - [ ] A type-time failure stops before any run-time effect is attempted — regions are evaluated in ordinary execution order, not in a whole-program preflight; earlier effects and effects after a caught failure remain possible

### Expanding the type algebra

Grow a coherent algebra of descriptions, with a stated meaning for each operation before choosing its syntax. Membership, subtyping and equivalence are different questions: checking one value must not be mistaken for proving a relationship between types. Every new former must compose with existing types, reflection, diagnostics and the YAML round trip.

- [ ] Complete the value-set algebra
  - [x] Define types by the values they admit, with explicit top (`any`) and bottom (`never`, no inhabitants), distinct from the literal `null` type (`types_bottom` golden case)
  - [ ] Add intersections alongside unions; document their interaction with refinements, optional fields and open/closed records
  - [ ] Define union as least upper bound and intersection as greatest lower bound wherever the supported fragment can represent them; specify approximations explicitly
  - [ ] Explore difference, complement and negation only within a defined universe and documented decidable fragment
- [ ] Extend structured collection types
  - [ ] Fixed-length tuples and heterogeneous positions
  - [ ] Typed map keys
  - [ ] Record row constraints and explicit width/depth subtyping rules
- [ ] Add function-signature types beyond the current coarse `function` membership check and per-call annotations
  - [ ] Describe parameter and result types, including higher-order functions
  - [ ] Define parameter/result variance and callable compatibility
  - [ ] Express effect contracts, including the purity required of type-time functions
- [ ] Support named recursive types
  - [ ] Define guardedness, unfolding, equality and termination of checking
  - [ ] Distinguish recursive descriptions from erroneous evaluation cycles
- [ ] Make type relationships and equivalence explicit
  - [ ] Define the subtyping order and semantic equivalence separately from structural declaration `eq`
  - [ ] Specify refinement implication and constraint composition: which facts are decidable, require evidence or remain unknown
  - [ ] Normalize types without losing original declarations or provenance; state when normalization is canonical and preserves meaning
- [ ] Connect inheritance to the algebra
  - [ ] Combine inherited constraints by intersection
  - [ ] State default selection and explicit override rules separately from membership
- [ ] Integrate every new former with the existing type system
  - [ ] Document membership, subtyping, equivalence, reflection, serialization and failure behavior
  - [ ] Cover positive, negative and boundary behavior with golden cases

### Static checking and inference

Current annotations and refinements enforce contracts during evaluation. `type_time` explicitly evaluates pure code; it does not infer types or prove unexecuted code safe. The non-executing `check` command provides conservative diagnostics, while `check --evaluate` collects evaluation failures. The goals below extend those boundaries; the formal guarantees belong under "Strengthening and proving the type system."

- [ ] Define a statically checkable fragment and the guarantees a successful check provides
  - [ ] Check annotated function bodies without calling them
  - [ ] Check both conditional branches without executing their effects
  - [ ] Report dynamic or value-dependent obligations as unchecked rather than passed
- [ ] Decide the scope of type inference
  - [ ] Infer supported expression and binding types using the authoritative type algebra
  - [ ] Specify where annotations are required and how inferred types interact with unannotated code
- [ ] Connect static results to runtime contracts
  - [ ] Reuse the authoritative membership and subtype rules instead of maintaining competing semantics
  - [ ] Retain runtime checks for obligations outside the supported static fragment
  - [ ] Produce structured diagnostics with source blame and explicit unknown/resource-exhausted outcomes

### Strengthening and proving the type system

The goal is justified guarantees, with the assumptions and limits visible. Executable examples and randomized checks find counterexamples; they are not proofs. Arbitrary Helix refinement predicates make general implication and equivalence undecidable, so a bounded checker must distinguish proved, disproved and unknown rather than silently treating exhausted resources as an answer.

- [ ] Write a small formal core with syntax, value/type domains, evaluation rules, typing judgments and effect judgments; state precisely which implemented features it covers
- [ ] State and prove the algebraic laws for the supported fragment: subtype reflexivity/transitivity, equivalence, union/intersection bounds, commutativity, associativity, idempotence, absorption, and top/bottom identities
- [ ] Prove membership and subtyping algorithms sound against the core semantics; claim completeness only for explicitly identified fragments
- [ ] Specify decidability and resource bounds per operation; expose unknown or exhausted checks as structured results, distinct from rejection and from malformed declarations
- [ ] Prove normalization preserves denotation; prove termination and, if canonical forms are promised, confluence or uniqueness up to the documented equivalence
- [ ] Prove preservation and progress for the typed core, accounting for structured errors, effects and the boundary with unannotated code; state what successful checking actually rules out
- [ ] Prove type-time evaluation cannot perform run-time effects and is deterministic under its declared inputs; separate resource-bounded failure from mathematical termination
- [ ] Prove inherited constraints are preserved by composition and parent order cannot change a successful result; treat defaults and override conflicts under their own rules
- [ ] Prove type serialization/reflection preserve meaning and that checked annotations retain their meaning after save/reload; extend the argument to suspended execution when resume exists
- [ ] Mechanize the core definitions and proofs in a chosen proof assistant, recording assumptions and trusted components; keep a precise correspondence with the Rust implementation
- [ ] Add reproducible algebra-law, generated counterexample and differential checks through `./manage.py`, reusing the golden and round-trip infrastructure; retain minimized failures as regression cases
- [ ] Make proof checking discoverable through `./manage.py` and run it alongside implementation checks; a changed semantic rule requires updating its proof obligations and executable cases together

### Type classes and inheritance

An abstract class is an incomplete description: some fields fixed, some defaulted, some demanded but left open. Inheriting is composing descriptions, and a concrete type is one with nothing left open. This class algebra composes fields, defaults and constraints into a plain flattened type that could have been written out by hand. Composition of behavior is a separate design goal below, built on ordinary values and functions. Classes are meant to be small and combined freely (`server` plus `has_ssh` plus `monitored`) rather than arranged in a deep tree. Because several parents will disagree, the merge rules are the heart of this group: they must be few, written down, deterministic, and they must refuse to guess. An ambiguity the author did not resolve is an error, never a precedence rule that picks a winner quietly.

- [ ] Abstract type classes: declare required fields and constraints without a concrete value (`machine`, `server`, `has_ssh`)
  - [x] A class field is one of: fixed to a value, defaulted, or demanded and left open
  - [x] A class can carry constraints over several of its fields (a refinement on the whole record)
  - [ ] A class is a type value: everything under "First-class types" applies to it
- [ ] A type inherits from one or more classes; the result is the merge of their fields, defaults and constraints
  - [x] Single parent
  - [x] Several parents with disjoint fields
  - [x] Several parents sharing a field they agree on (including diamonds: one ancestor reached twice merges once; `class_conflicts`, `class_constraints`, `type_algebra`)
  - [ ] Classes inheriting from classes, to any depth
  - [ ] The result is a plain flattened type: emitted, it reads as if written out by hand
  - [x] Constraints from every parent all hold on the realized result (`class_constraints`; abstract composition defers whole-record checks until realization)
- [ ] Documented, deterministic merge and override rules (which parent wins, what counts as a conflict, how lists and maps combine)
  - [ ] Decided: the merge model (see Open questions) and whether inheritance is nominal, structural or both
  - [x] Field types from two parents: when they combine and when they conflict (`class_conflicts`; declarations must agree or the child must explicitly narrow every parent)
  - [ ] Defaults: a child's explicit value overrides a parent default; two differing parent defaults are a conflict
  - [ ] Lists and maps: one stated rule each (replace, append, or merge by key), with an explicit way to ask for another
  - [ ] Parent order never changes the result
  - [ ] The rules are written in `LANGUAGE.md`, with a golden case per rule
- [ ] Conflicts between parents are type-time errors that name both sources, never silent last-writer-wins
  - [ ] The error gives the field and the path and line of each disagreeing declaration
  - [ ] Every conflict in a declaration is reported in one pass
  - [x] A conflict is resolved only by an explicit override in the child, and the override is visible in provenance (`class_conflicts`, `dogfood_class_conflict_fixed`; class names, not source spans)
- [ ] An abstract class cannot be realized until every required field is filled; the error lists what is missing and which class demanded it
  - [x] Using an abstract type where a concrete one is needed is an error, not an implicit default (`classes.not_yet`; required fields without defaults prevent realization)
  - [x] All missing fields are listed at once, each with the class that demanded it (`classes`, `type_algebra`; `data.missing` carries `field` and `required_by`)
  - [ ] An override that violates a parent's field type or constraint blames the override and names the parent
- [ ] Provenance: for any field of a resolved type, the state shows which class or override supplied it
  - [ ] Each field records its source (class or override) as a root-relative path with line
  - [ ] Overridden values are shown too: what lost, and to what
  - [x] Provenance is queryable as data from Helix, not only visible in the output (`classes`, `class_conflicts`; ordinary `provenance` maps)
  - [ ] Provenance round-trips and does not make the resolved type harder to read or diff
- [ ] Parametric classes: a class takes arguments (`[web_server, {port: 8080}]`) and yields a class
  - [x] It is an ordinary `fn` returning a class: no separate generics mechanism
  - [x] Arguments are themselves type-checked
  - [ ] Provenance records the arguments a class was instantiated with

### Tools for composition over inheritance

Take the Smalltalk intuition of sending a message to a value, with a mathematically small, pure core: an object is readable data with behavior, and a message application is a function of the receiver and arguments. Prefer composing small capabilities to growing class hierarchies. These are design goals; syntax and dispatch rules remain to be settled.

- [ ] Compose objects from immutable records and existing first-class functions; methods reuse ordinary `fn` parameters, closures and result contracts
- [ ] Define message application as explicit receiver/name/arguments lookup and function application, with one documented rule for binding the receiver (`self`)
- [ ] Pure state transitions return a new value (and any result); the original receiver stays unchanged, and identical inputs give identical outputs
- [ ] Assemble small capabilities with explicit record composition; disjoint members combine, shared members require agreement or an explicit resolution, and parent order never silently picks a winner
- [ ] Delegate selected messages to a contained value; forwarding and receiver binding are explicit, with no hidden superclass chain
- [ ] Adapt capabilities through explicit member selection, renaming and exclusion, reusing ordinary map/function tools wherever possible
- [ ] Support polymorphism through a shared message contract; distinguish satisfying a capability from declaring an ancestor, and reuse the type algebra for compatibility
- [ ] Keep behavior discoverable as data: inspect available messages, contracts, captured bindings and composition provenance; missing or conflicting messages produce structured errors with source locations
- [ ] Serialize composed values, behavior and captured state through the existing YAML round trip; saving and reloading preserves message results without hidden host identity
- [ ] Document composition laws and boundaries: identity, associativity for compatible components, conflict behavior and observational equivalence; demonstrate them with golden cases and reproducible laws through `./manage.py`
- [ ] Demonstrate a small pure object built from independent capabilities, a delegated wrapper and an immutable update, without requiring inheritance or a parallel method evaluator

### Machine configs as type-time types

The config is the type; the real machine is the value. "Is this machine configured correctly" becomes "does this machine inhabit its type", drift is a machine that no longer does, and applying a config is making the value fit the type. That framing splits the work into two phases with a hard wall between them. Type time resolves classes, overrides and cross-machine constraints into a complete description using nothing but the source: no SSH, no facts gathered from hosts, no network. Everything that can be wrong with the description is found there, and the resolved description is a reviewable artifact in `git diff`. Only then does realization touch a machine, as an explicit, separate, resumable run built on effects (section 3) and suspend/resume (section 2), with its plan and progress as readable state. From Nix this takes pure evaluation before any change and reproducibility; from Ansible it takes agentless, incremental application to machines that already exist. What it rejects is Ansible's failure halfway through a play from an error knowable in advance, and Nix's opaque evaluation whose result cannot simply be read.

- [ ] A machine config is a concrete type inheriting from abstract machine classes; it is fully resolved at type time with no machine contact
  - [x] A small standard vocabulary of machine classes (packages, files, services, users), declared in Helix itself (`std:machine`, `std_machine` golden case)
  - [ ] A config with an unfilled or conflicting field fails at type time with the class errors above
  - [ ] Resolution makes no contact with any machine or network, enforced by the type-time contract and not by convention
  - [ ] Facts that only a machine knows are declared as typed inputs to realization, never read during resolution
- [ ] The resolved config serializes to YAML that reads as the complete, flattened description of the machine and diffs well in `git diff`
  - [ ] Stable key order and formatting, so an unchanged config produces no diff
  - [ ] A one-field change in a class shows as a one-line change in every machine that inherits it
  - [ ] Provenance is available without drowning the description (separable or opt-in)
- [ ] Same inputs, same resolved config: resolution is deterministic and pinned (imports, versions) so a config is reproducible
  - [ ] Imports are pinned (section 5) and the pins are recorded in the resolved config
  - [ ] The Helix version and state format version are recorded
  - [ ] Resolving twice, on two machines, yields byte-identical output
- [ ] Fleet-level checks at type time: constraints across several machine types (unique hostnames, every client's server exists, no port clashes)
  - [ ] A fleet is a typed collection of machine configs
  - [ ] Fleet constraints are ordinary Helix predicates over that collection
  - [ ] A violation names every machine involved, with paths
  - [ ] All fleet violations are reported in one pass
- [ ] Realization is a separate, explicit step: compare a resolved config with a machine's observed state and produce a plan as data
  - [ ] Observed state is gathered by effects into a value of a declared type
  - [x] The plan is plain YAML: ordered steps, each naming the field of the type it realizes (`machine_fleet.plan_web1`; `converge` emits canonical field-ordered `set` steps)
  - [ ] An empty plan means the machine inhabits its type — `converge` compares configured values and ignores extra observed fields, while structural class membership uses a closed record; equivalence with full class membership is not established
  - [x] Planning changes nothing on the machine (dry run is the default, applying is the explicit act): `drift` and `converge` only compare supplied data (`machine_fleet`); effectful observation and complete machine application remain separate obligations
- [ ] Applying a plan uses the effect system (section 3), is idempotent, and suspends/resumes per step (section 2) with the pending step visible in the state
  - [ ] Each step is an explicit effect recorded in the state before it runs
  - [ ] A failed or interrupted apply resumes from the pending step, not from the start
  - [ ] Applying twice changes nothing the second time
  - [ ] After applying, a fresh plan is empty
- [ ] Drift report: what the machine has that its type does not say, and the reverse
  - [ ] Both directions reported as data, per field, with the type's provenance for each
  - [x] Decided and documented: what counts as unmanaged (ignored) versus drift — extra observed fields appear in `drift.extra` but generate no removal steps (`machine_fleet`, `src/builtins/typing/machine.rs`, `LANGUAGE.md#classes-machines-and-drift`)
- [ ] Secrets are referenced by a typed handle, never inlined into a resolved config or saved state
  - [ ] A secret type whose values cannot be emitted: resolved configs and saved states show the handle only
  - [ ] Secrets are fetched only during realization, by an explicit effect
  - [ ] Error messages, plans and drift reports never contain a secret value

---

## 9. Dogfooding milestones

Real programs, in order of ambition, grouped by what unlocks them. Each one is a checkbox only when I actually use it, not when it merely could work. Keep each one in the repo (a golden case in `tests/cases/` once its output is deterministic) and write down what was awkward: that friction is the real roadmap.

### With today's pure core
- [ ] Classic small problems as golden cases: FizzBuzz, primes, word frequency over inline text
- [ ] A 100+ line program (an expression evaluator, a sort, a small state machine) written without fighting the language
- [ ] One real data-reshaping task: paste YAML data in, compute a report, use the emitted `root`
- [ ] Compare two inline configuration snapshots and use a report of added, removed and changed keys to review a real change
- [ ] Validate an inline project inventory (required fields, duplicate names, broken references) using `assert`/`try` so one bad record is reported as error data without stopping the rest, and fix the real data from its findings
- [ ] Compute a dependency order for a small set of real tasks; missing and cyclic dependencies surface as raised, kind-matched errors that a caller recovers from, not special-cased return values
- [ ] Reconcile two inline inventories or expense lists, with `assert` invariants (totals balance, no negative stock) whose failures name the offending record, and use them to correct the originals
- [ ] A friction log: every awkward moment from the programs above, especially around error handling, becomes a charter item or a deliberate "no"

### Pure-core test coverage and friction

Golden coverage is evidence of executable examples, not evidence of real-world adoption; the usage milestones above remain open.

- `dogfood_classics`: FizzBuzz through 20, primes below 30, and word frequencies over inline text, including empty inputs, prime boundaries, repeated spaces and Unicode words. Friction: no string split builtin; tokenization folds over character slices and explicitly handles delimiters. Trial division is suitable only for small inputs. These are executable examples; the real-use milestone remains open.

- `dogfood_config_diff`: added/removed/changed keys, nested value changes, null versus absence, unchanged and empty snapshots. Explicit references now allow direct report maps; nested changes are still reported at the top-level key.

- `dogfood_project_inventory`: missing required fields, duplicate names and dangling dependencies, plus valid and empty inventories. A declared record type now supplies required fields and collected nested violations; malformed rows retain their diagnostics without aborting duplicate and reference reporting.

- `dogfood_dependency_order`: stable ordering of diamond dependencies, missing references, cycles, self-dependencies and empty graphs; independent work completes before blocked tasks are reported. Friction: immutable maps have no removal operation, so each batch rebuilds the pending map. Blocked results preserve the graph rather than claiming every blocked node belongs to a cycle.

- `dogfood_expense_reconciliation`: aggregate duplicate accounts and refunds in integer cents; report mismatched totals and accounts present on only one side, including zero balances; balanced and empty ledgers yield no findings. Friction: currency units and matching keys are conventions in user code, with no decimal-money type.

- `dogfood_work_queue`: stable priority sorting and dispatch through `std:list.sort_by`, with negative priorities, equal-priority payloads, sorted/reversed/empty/singleton inputs, independent calls, idempotence and source-order preservation. The original 100+ line example now shares its sort implementation with the standard library. Friction: insertion sort repeatedly copies lists and is suitable only for small queues.

### Errors as a first-class feature (section 4) — executable coverage

`dogfood_errors` covers independent malformed-row validation, a calculator with per-input recovery, kind-based retry histories, domain wrapping with retained causes, assertion-led caller repair and cleanup on success/failure. Reports retain input positions and full errors; later valid rows still finish.

The paired `dogfood_errors_regression` / `dogfood_errors_fixed` cases preserve a minimized failure and its one-input correction (`count: 0` to `count: 1`). Their saved-state diff explains the assertion failure and corrected result, using the existing golden regression workflow.

Friction: lazy bindings require assertions in the result dependency chain; wrapping retains the original error explicitly under `cause`. Pure cleanup uses a raised error to witness execution, with a separate successful-cleanup case preserving the original failure. These examples do not establish external retry or resource-management guarantees. Parser-document coordinates appear in messages; structured spans identify the parsing actor, so reports retain source and index.

### With suspend/resume (section 2)
- [ ] A long computation stopped midway, its state read in `git diff`, resumed to the same result as an uninterrupted run
- [ ] Fix a mistake by hand-editing a suspended state and resuming it
- [ ] Resume a run that stopped on an error after fixing its cause by hand, without redoing the work that preceded it
- [ ] Stop and resume inside nested function calls and iteration; inspect the saved locals and remaining work without consulting a trace
- [ ] Move a suspended run to another checkout or machine and finish it using only its state and declared dependencies
- [ ] Keep two edited branches of one suspended run, finish both, and use their state diff to explain why the results differ

### With effects and imports (sections 3–5)
- [ ] Replace one small shell script with Helix
- [ ] A config-transform pipeline (read YAML in, compute, write YAML out)
- [ ] Audit real project files for broken local links or missing referenced files and use the report to repair them
- [ ] Turn a real CSV export into a JSON or YAML summary used in a recurring report
- [ ] Generate a changelog draft from git history, with the source commits retained in the saved state for review
- [ ] Call a paginated HTTP API, save progress between pages, and resume after a failed request without fetching completed pages again
- [ ] Run a batch file transformation with a preview, an explicit apply step, and a manifest sufficient to undo the changes
- [ ] Share one imported helper between two real scripts and update it without copying code between them
- [ ] Recover a failed external-command workflow after fixing its input, without repeating already completed successful steps
- [ ] A task runner for this repo (`./manage.py` logic expressed in Helix)
- [ ] A long-running workflow that suspends on an external event and resumes days later
- [ ] A program that inspects and edits another Helix program's saved state
- [ ] Helix running its own test suite

### With daily tooling and trust (sections 6–7)
- [ ] Diagnose a real failed script using only its saved state, structured error and inspection tools; turn the failure into a regression case
- [ ] Run an unfamiliar Helix program with restricted file, command and network permissions; understand and grant only the effects it needs
- [ ] Upgrade Helix and resume a state saved by the previous version, following the documented compatibility or migration path
- [ ] Give someone else a useful script and have them install Helix, run it and adapt it using only the documentation

### With types and machine configs (section 8)
- [x] Replace the hand-rolled field checks in `dogfood_project_inventory` with a declared type; the type error report matches or beats the hand-written one
- [x] Write a function that takes a type and returns a derived type, and use it to remove real duplication between two declarations — `dogfood_derived_changes` shares audit metadata and partial-update policy between file and service proposals using `std:types.optional_fields`.
- [ ] Model my own machines as types: a laptop and a server sharing abstract classes, differing only in what they override
- [x] Introduce a deliberate conflict between two parent classes and fix it from the type-time error alone — `dogfood_class_conflict` identifies the field and parents; `dogfood_class_conflict_fixed` supplies an explicit child override.
- [ ] Review a config change as a `git diff` of the resolved type before anything is applied
- [ ] Manage one real piece of one real machine (a dotfile set, a package list, a service) from its Helix type for a month
- [ ] Rebuild a machine, or bring up a second one, from its type alone and record everything that had to be done by hand

### The bar
- [ ] A week where I reach for Helix first without thinking about it
- [ ] Three recurring personal or repo workflows stay in Helix for a month; record each fallback to Python or shell and the reason

---

## 10. Super dogfood

Implement Helix's own language services and development workflows in Helix. The goal is to replace host implementations with ordinary, inspectable Helix modules, beyond merely writing example programs. Start with pure policy over data; keep a small Rust bootstrap for parsing, primitive values and operations, evaluation, source attribution, and effect execution until a replacement can preserve their contracts.

### Already within reach of the pure core

- [x] Standard-library arithmetic and list helpers are Helix source: `std/math.yaml` and `std/list.yaml` already implement min/max/clamp, sum/product/reverse/range, exercised by the standard-library golden cases. Extend these modules rather than adding equivalent Rust builtins.
- [x] Collection and text algorithms: sorting, grouping, deduplication, map merge/removal, splitting and joining as imported Helix functions over the existing collection primitives. Reuse the algorithms in the dogfooding cases; document copying costs and iteration limits before treating them as general-purpose library functions.
  - [x] Stable grouping by a string-valued callback in `std:group.by`, covered by `std_group`; preserves item order within each group, with canonical string-key order for groups and documented copying costs and iteration limits. Real tooling caller: `./manage.py inventory` invokes `tooling/inventory.yaml` to group repository files by directory and saves replayable input/state; covered by `dogfood_tooling_inventory` and CLI contracts.
  - [x] Stable comparator sort (`sort_by`) extracted from the work-queue example, plus first-occurrence deduplication (`unique`), in `std:list`; covered by `std_list_algorithms` and `dogfood_work_queue`. Both use quadratic scans/copying and runtime iteration limits; intended for small inputs.
  - [x] `std:list` access/slicing, enumerate/zip, one-level flatten/flat-map, short-circuit searches, quantifiers, count/partition, grouping/frequencies and default sorting; contracts and boundary cases live in `std_list_helpers`.
  - [x] Shallow right-biased merge and single-key removal in `std:map`, covered by `std_map`: stable key order, missing/null distinction, nested replacement, empty and Unicode keys, and checked arguments. Immutable copies can cost quadratic time/allocations; runtime iteration limits apply.
  - [x] Literal splitting and joining in `std:text`, covered by `std_text`: Unicode, empty fields, separator policy and argument contracts; copying costs and iteration limits are documented in `LANGUAGE.md`.
- [x] Configuration diff, dependency ordering and validation reports as reusable Helix modules, extracted from the existing dogfooding cases. Their policies and error recovery belong in Helix; primitive lookup, equality and structured error attribution remain runtime services.
  - [x] Shallow configuration comparison in `std:diff.compare`, extracted into the authoritative module used by `dogfood_config_diff`; `std_diff` covers ordering, missing/null distinctions, nested values and invalid inputs.
  - [x] Indexed batch validation in `std:validation.batch` reuses `std:test` report policy and retains inputs and original errors; `std_validation` covers continuation and boundary contracts, and `dogfood_errors` uses it for YAML row validation.
  - [x] Stable dependency planning in `std:dependency.plan`, used by `dogfood_dependency_order`; `std_dependency` covers blocked graphs, missing prerequisites and argument contracts.
- [x] Type descriptor construction and transformation: `std:type_descriptor` owns assembly for `list_of`, `map_of`, `optional`, `default`, `union`, `enum`, `literal`, `record`, `with_field` and `secret`; the corresponding Rust assembly is removed. Public calls retain shared runtime type/field validation, default checking and argument blame, and execute the policy through ordinary evaluator calls. `type_descriptor_policy` is the golden contract; `type_algebra` checks independent constructor results, invalid arguments and records through 256 fields. Class/refinement internals and the authoritative checker remain Rust. Immutable record updates can incur quadratic copying costs.
- [x] Drift and convergence planning: `std:convergence` reuses `std:diff` for comparison and ordered plans over desired and observed maps; the replaced Rust comparison/plan implementation is removed. Public `drift`/`converge` retain class/input validation and call the policy through the existing engine. `std_convergence` covers missing versus null, nested changes, stable steps, extra-field preservation, empty plans and pure phases; `type_algebra` supplies an independent 128-field oracle and `execution` checks retained continuations. Observation and application stay separate effects.

### Next: types and callable values make the larger pieces practical

The runtime supports types, classes and callable values beyond the established pure core. The remaining migrations below do not imply that the complete section 8 contract already works.

- [ ] Type validation and checking: move descriptor traversal, nested violation collection, unions, records and refinement orchestration from `src/builtins/typing/ty.rs` into Helix. Needs dependable primitive type inspection and first-class predicate calls; preserve declaration errors versus rejected values versus failed predicates, bounded traversal, deterministic diagnostics and source blame. Keep one authoritative checker used by both annotations and explicit checks.
- [ ] Class composition and realization: implement inheritance conflict detection, defaults, required-field checks, flattening and provenance from `src/builtins/typing/class.rs` in Helix, building on the shared checker and the supported subtype rules. Requires callable predicates and sufficient provenance reflection; preserve explicit conflict resolution and parent-order independence. Future algebra and subtyping policy should live beside this code rather than grow another Rust policy engine.
- [ ] Machine vocabulary and planning policy: declare packages/files/services/users, fleet constraints, dependency ordering and plan validation in Helix (section 8). Given typed observations, compute the complete plan as data; host adapters expose narrowly defined effects. Resume and retry policy can follow once pending effects and continuations serialize (section 2).

### With a few explicit runtime services

- [ ] Canonical formatting and state inspection: implement layout, state summaries and eventually migrations in Helix. Replacing `src/emit.rs` needs lossless scalar/source reflection, scalar-to-text and escaping support, plus a bootstrap output path that does not recursively invoke the formatter. Preserve explicit literal boundaries, call/reference forms, source spans and the existing round-trip fixpoint.
- [ ] Static checks as Helix graph passes: dependency/cycle diagnostics, annotation discovery and type reports for `helix check`. Requires access to the unevaluated graph, parent links, paths and spans without triggering effects; reuse the authoritative checker. Runtime cycle guards and resource limits remain enforced by the bootstrap.
- [ ] Test and task orchestration: express case discovery, golden comparison, round-trip checks, mutation generation and build/lint invocation in Helix once file, process, argument and parsing effects exist (section 3). Keep `./manage.py` as the obvious root entrypoint and bootstrap launcher; replace its orchestration behind that interface rather than create a competing workflow. Host-level crash checks must still detect a broken interpreter.
  - [x] Pure test policy in `std:test`: assertions, expected errors, per-case recovery, table/state fixtures, bounded properties, differential checks and serializable reports. `dogfood_testing` composes selection, failure reruns, fixture factories, shrinking and nested suites; `std_test_failure` proves a failed report becomes a structured VM error. Host filesystem/process orchestration remains pending.
  - [x] Robustness mutation policy in `std:fuzz.plan`: Helix selects ordered source-slice plans for line prefixes, line deletions and character prefixes. The production Python harness materializes one variant at a time and independently supervises interpreter failures. `std_fuzz` and harness parity contracts cover Unicode boundaries, duplicate ordering, the largest existing fixture and malformed generator results. Full host orchestration remains pending.
  - [x] Golden-test decision policy in `std:test.golden_plan`: production tests use Helix to decide deterministic-output failures, exact expected-text comparison and ordinary-root replay outcomes. Python captures observations, enforces the policy response contract and independently rejects interpreter failures. Golden fixtures and harness fault injection check the policy and its host boundary. Discovery, process execution and build/lint orchestration remain host responsibilities.
  - [x] Production suite orchestration in `tooling/test_policy.yaml`: `./manage.py test` uses Helix for suite selection/order, standard-library prerequisites, blessing skips, result statuses and report aggregation. Python executes the plan, preserves captured observations and independently rejects missing/unauthorized tasks or false-success responses. `dogfood_test_orchestration` and `harness` cover independent expected plans, policy crashes, malformed responses, retained failures and realistic report sizes. Standalone startup checks remain build-free. Filesystem discovery, process supervision/concurrency and build/lint invocation remain host-owned, so the parent milestone stays open.
    Friction: an output-map field shadows an enclosing binding of the same name; use a distinct binding or `dict` to avoid self-reference. Computed maps in `let` require explicit `get` for field access; lexical dotted references are not general computed-value lookup. Lazy local bindings can recompute a comparison inside each callback; convergence passes computed comparisons as eager function arguments so callbacks reuse them.

### Migration bar

Move one complete responsibility at a time: a Helix module becomes the authoritative implementation, production callers use it, and the replaced Rust policy is removed. A forwarding wrapper around the same Rust implementation does not count. Preserve public syntax, structured errors, locations and state round trips; prove parity through `./manage.py test` golden cases, malformed-input coverage where applicable, and realistic size checks. A temporary comparison against the old implementation can establish parity, but must not leave two policy engines to maintain. Defer a self-interpreter or YAML parser rewrite until lossless graph reflection, explicit continuations and bootstrap recovery make it a concrete improvement.

---

## 11. Replacing Ansible

Replacement means operating real machines reliably within a declared support scope, not merely invoking shell commands from YAML. Start with Linux hosts reached locally or over SSH; expand the supported platforms and resources from demonstrated needs. Playbook syntax compatibility and parity with every Ansible collection are not initial requirements. A scoped replacement must state what it cannot manage.

Reuse the type/class algebra, `std:machine`, `drift`, `converge`, `std:dependency` and the existing effect engine. Section 8 owns configuration resolution, pure planning and realization semantics; sections 2–3 own snapshots and host effects. This section owns the operational capabilities and acceptance evidence needed to turn those foundations into an Ansible replacement. Existing data comparison and in-memory effect records are foundations, not completed machine management.

### First useful replacement: one Linux host

- [ ] Replace one real configuration playbook with an end-to-end Helix configuration
  - [ ] Declare a package, a managed configuration file and a service using the existing machine vocabulary
  - [ ] Observe their actual state through authorized effects and produce a plan using section 8's realization contract
  - [ ] Review and explicitly apply that plan, then verify the resulting state independently
  - [ ] A second run performs no mutations; deliberately introduced drift produces only the necessary repair
  - [ ] Demonstrate both local execution and SSH execution on a disposable host
  - [ ] Record the supported distribution, resource semantics and remaining limitations alongside the example

### Inventory, connections and observed state

- [ ] Select and describe hosts without implicit configuration precedence
  - [ ] Typed inventory supports stable host identities, groups, connection settings and explicit overrides with provenance
  - [ ] Host/group selection and exclusions resolve to a reviewable target list before mutation
  - [ ] Dynamic inventory is an explicit host input with validation; discovery never runs inside pure configuration resolution
  - [ ] Missing hosts, duplicate identities and incompatible settings produce structured diagnostics
  - Evidence: `./manage.py test --suite host_inventory` exercises the pure
    `std:inventory` module with static and supplied dynamic data, an independent
    selection oracle, ordered override histories and invalid-input diagnostics.
    Validation includes unselected hosts and empty inventories; failures return
    no targets. The [runnable example](tests/programs/host_inventory.yaml) and
    [inventory contract](LANGUAGE.md#host-inventory) document the versioned schema,
    first-error reporting and bounded in-memory support. Connection descriptions
    confer no authority; discovery, SSH adapters and resource application remain
    separate milestones below.
- [ ] Supply dependable local and SSH host adapters through the existing effect boundary
  - [ ] SSH supports host-key verification, existing key/agent authentication, connection reuse and jump-host configuration
  - [ ] Privilege escalation is explicitly authorized and distinguishes connection identity from execution identity
  - [ ] Remote argument transport and file transfer preserve bytes and prevent accidental shell interpretation
  - [ ] Connection, authentication, privilege, transport and remote-command failures remain distinguishable
  - [ ] Document and check managed-host prerequisites; bootstrap missing prerequisites through an explicit supported path
- [ ] Gather typed facts and resource observations for realization
  - [ ] Detect supported operating systems, package managers and service managers before selecting adapters
  - [ ] Observations carry host identity and freshness information; unknown or inaccessible state is never treated as absence
  - [ ] Cached facts have explicit invalidation rules; relevant state is refreshed before applying a potentially stale plan

### Resource providers and change semantics

- [ ] Establish one discoverable provider contract for observation, planning, application and verification
  - [ ] Providers declare argument/result types, capabilities, supported platforms, check/diff support and failure behavior
  - [ ] Distinguish unchanged, changed, failed, skipped and unsupported outcomes using structured data
  - [ ] Pure plan generation shares section 8's semantics; a dry run never silently applies a resource whose preview is unsupported
  - [ ] Re-observe after mutation and report unmet postconditions instead of trusting a successful command exit alone
  - [ ] Define ownership and deletion explicitly so absent configuration does not accidentally remove unmanaged resources
  - Evidence: `./manage.py test --suite provider_contract --suite type_algebra --suite library`
    checks `std:provider` with typed callbacks, pure data-only plans compatible
    with `converge`, unsupported-preview gates, exact fresh review matching,
    authorized file changes, no-op reapplication, drift repair and explicit
    removal. Independent host-state assertions cover preservation of unmanaged
    files, mutation followed by failure and unmet postconditions. See the
    [runnable example](tests/programs/provider_contract.yaml) and
    [provider contract](LANGUAGE.md#resource-provider-contract). Callbacks are
    trusted provider implementations, capabilities remain caller grants, and
    revision identity is author-declared. Pure phases currently exclude linked
    file imports; locking, durable recovery and production resource providers
    remain separate milestones.
- [ ] Implement a useful initial resource library behind that contract
  - [ ] Packages and repositories: presence, absence, versions, metadata refresh and package-manager locking
  - [ ] Files, directories and links: content, ownership, permissions, atomic replacement and explicit removal
  - [ ] Configuration rendering: deterministic templates, validation before installation and reviewable content diffs
  - [ ] Services: enabled/running state, reload/restart policy and readiness checks
  - [ ] Users and groups: membership, ownership implications and authorized keys with explicit removal semantics
  - [ ] Artifacts: authenticated retrieval where needed, pinned versions and integrity verification before use
  - [ ] Arbitrary commands: explicit guards and change/failure predicates; no implied idempotence or reversibility
- [ ] Compose resources and reusable configurations without a second execution model
  - [ ] Dependencies reuse `std:dependency` where suitable and reject missing prerequisites and cycles before mutation
  - [ ] Change notifications coalesce service reloads/restarts and run only after their prerequisites succeed
  - [ ] Reusable configuration modules expose typed inputs, outputs and versioned dependencies using existing imports and functions
  - Evidence: `./manage.py test --suite resource_composition --suite library`
    checks `std:orchestration` against an independent dependency-order oracle,
    observable callback/file effects, failure gates, coalesced handlers and retained
    VM continuations. Full graph validation precedes executor callbacks; handlers
    run once after resources and require successful prerequisites and notifiers.
    The [typed module example](tests/programs/resource_composition.yaml) includes
    relocatable, vendored dependencies with explicit provenance. See the
    [composition contract](LANGUAGE.md#resource-composition) for sequential ordering,
    resource-only handler prerequisites and partial-failure reports. Real service
    providers, durable recovery and rollback remain separate milestones.

### Fleet orchestration and failure recovery

- [ ] Run across a fleet with bounded, observable scheduling
  - [ ] Support concurrency limits, serial batches, canaries and explicit failure thresholds
  - [ ] Express cross-host dependencies and controller/delegated tasks with unambiguous execution targets and authority
  - [ ] Gate rolling changes on health checks and stop subsequent batches when the declared policy fails
  - [ ] Support reboot, disconnect and reconnect workflows with bounded readiness waits
- [ ] Make long-running operations practical — bundled local synchronous command adapter
  - [ ] Replace the bundled host's fixed 10-second command deadline with caller-controlled bounded timeouts
  - [ ] Support bounded output capture and progress reporting for package installs and deployments
  - [ ] Define cancellation and descendant-process cleanup so a timed-out controller does not silently leave mutations running
  - [ ] Retry only explicitly classified transient failures with bounded attempts and delays
  - Evidence: `./manage.py test --suite long_commands --suite effects --suite library`
    exercises real subprocesses, extended deadlines, cumulative capture budgets,
    metadata progress, classified retries, cancellation through both the interpreter
    and project wrapper, descendant liveness, and callback failure/panic cleanup.
    The caller owns all policy; time and output budgets span attempts and delays.
    Unix process groups provide local cleanup, not rollback or containment of
    deliberately detached descendants. SIGKILL/controller crashes and uncertain
    remote mutations remain obligations of connection adapters and durable recovery.
    See [bounded command operations](LANGUAGE.md#bounded-command-operations) for
    supported semantics and limits; this does not complete resource providers or
    qualify a real Ansible workload.
- [ ] Recover safely from partial application, building on section 2's durable snapshots
  - [ ] Persist plan identity, target identity, provider versions and step outcomes; resumed runs obtain fresh caller grants
  - [ ] Distinguish not started, completed, failed and uncertain outcomes when a controller or connection dies during a mutation
  - [ ] Resolve uncertain outcomes by observation or provider-supported deduplication before replay; branch-local effect IDs do not establish exactly-once external execution
  - [ ] Detect stale plans and conflicting concurrent controllers through preconditions and suitable locking
  - [ ] Expose partial success and pending work; recovery never reports an entire fleet successful because some hosts completed
  - [ ] Declare compensations and irreversible operations explicitly; external rollback is not implied by immutable VM state

### Credentials, operator workflows and extensibility

- [ ] Complete section 8's secret-reference contract throughout the operational stack
  - [ ] Resolve credentials only at authorized host boundaries and support external secret providers
  - [ ] Prevent secret disclosure through command arguments, captured output, errors, diffs, logs and persisted state
  - [ ] Separate inventory data from authority; remote credentials, escalation and provider capabilities remain caller grants
- [ ] Provide discoverable configuration-management commands and reports
  - [ ] Expose resolve, observe, plan, apply and verify workflows with target selection and explicit apply authorization
  - [ ] Report per-host/per-resource outcomes, source paths, changes and failure causes as concise text and versioned machine-readable data
  - [ ] Document exit statuses for clean runs, drift, partial failure and invalid configuration for CI use
  - [ ] Provide a supported installation and upgrade path with clear controller/managed-host compatibility requirements
- [ ] Grow provider coverage without coupling the interpreter to every managed system
  - [ ] Publish a versioned provider interface with capability checks, dependency pinning and compatibility diagnostics
  - [ ] Add conformance tests that third-party providers can run through the same project tooling
  - [ ] Publish a support matrix; qualify additional operating systems, cloud APIs and network devices separately as demand requires
  - [ ] Document migration of representative inventories, reusable roles, handlers and playbooks, including unsupported semantics

### Evidence required before calling it a replacement

- [ ] Maintain repeatable infrastructure tests behind `./manage.py`, using disposable hosts and explicit test capabilities
  - [ ] Exercise each supported provider/platform combination against actual host state, not only mocked command output
  - [ ] Verify clean installation, repeated no-op application, drift repair, preview accuracy and preservation of unmanaged state
  - [ ] Inject authentication failures, permission denial, stale observations, network loss and controller death at mutation boundaries
  - [ ] Verify recovery, secret redaction, target selection, concurrency limits and failure thresholds independently of Helix's own reports
  - [ ] Measure realistic fleet sizes, slow operations and retained execution history against declared resource budgets
- [ ] Retire Ansible for a declared real workload after a documented migration trial
  - [ ] Establish equivalent intended host state and operator-visible failures without requiring identical task syntax
  - [ ] Operate routine changes and recover from a deliberately interrupted deployment using Helix alone
  - [ ] Keep examples, provider support claims and operational limitations aligned with the tested implementation

---

## Open questions

Decide these before the features that depend on them.

- [x] How are user-defined actors declared in YAML so they stay readable? `{name: {$call: [fn, [params], {$ref: body}]}}`; called as `{$call: [name, ...]}`
- [x] What is the evaluation model for effects: pull, push, or explicit yield? Explicit yield: `{$call: [effect, {$ref: operation}, {$ref: payload}]}` suspends until a caller-authorized host resolves the request.
- [ ] Does a suspended state embed the source, reference it, or both?
- [x] Types are opt-in outside configs: unannotated code retains its behavior; `expect` and function parameter/result contracts enforce declared types (`types_annotations`; `LANGUAGE.md`).
- [ ] Is inheritance nominal (declared parents) or structural (anything with the right fields satisfies a class), or both?
- [x] Type time is an explicit `{$call: [type_time, {$ref: expression}]}` region, with `phase: type` on failures; `check --evaluate` collects diagnostics with the same evaluator, while plain `check` is non-executing (see `LANGUAGE.md`).
- [x] How do parent classes merge? Explicit agreement or child resolution: incompatible declarations/values conflict, child field redeclarations must narrow every parent, and lists/maps are replaced whole. `LANGUAGE.md#classes-machines-and-drift` and `class_conflicts` describe the implemented model; stronger order-independent provenance and richer merge operators remain open above.
- [ ] What is the story for secrets in persisted state?
- [ ] Which parts of `flintwinters/helixcpp` are worth carrying over, and which are better left behind?
