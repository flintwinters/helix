# Helix Charter Cockpit

**Goal:** Helix is a language I can reach for every day, for real scripts and automations, without apologizing for it.

The test for "daily driver" is simple: I start a task in Helix instead of Python or shell, I finish it, and I can open the saved state afterwards and understand exactly what happened.

How to use this file: tick a box only when the feature works end to end *and* has a golden case in `tests/cases/` (run `./manage.py test`). A feature that only works in a demo stays unticked. Edit freely; this file is for humans, while `AGENTS.md` holds the agent-facing architecture and invariants.

**Where it is headed:** the niche Nix and Ansible occupy, declarative machine configuration, approached through types. A machine config is a type, fully resolved at type time and inheriting from abstract machine type classes; section 8 holds that road. Daily-driver scripting comes first because the config language has to be a language worth using.

**Where we are:** a pure, resource-bounded expression language with first-class functions, declarative types, annotations, class composition and explicit type-time evaluation. File imports and pure error recovery work; full suspend/resume and effectful machine application remain ahead.

---

## 0. What exists today

- [x] Parse YAML source into an object graph with parent links and source spans
- [x] Root-relative node paths (`/inner/y/1`)
- [x] Vector-form expressions, actor first: `[add, x, 1]`
- [x] Lexical symbol lookup through parent links, nearest definition wins
- [x] Integer `add`, `sub`, `mul` with overflow checking
- [x] Structured errors with kind, message, path, line and column
- [x] Cycle detection for self-referential values
- [x] Finished state serializes to YAML and re-running its `root` is a fixpoint
- [x] `./manage.py` entrypoint with golden-case tests
- [x] Structural linting through the hyperlinter submodule

---

## 1. Core language: enough to compute things

The minimum for writing a non-trivial program.

### Values
- [x] Strings (literal, concatenation, length, slicing)
- [x] Floats
- [x] Booleans and null as first-class values in expressions
- [x] Lists with index, length, append, concat
- [x] Maps with get, set, keys, has
- [x] Clear, documented type rules (no silent coercion) — see `LANGUAGE.md`

### Operators
- [x] Comparison: `eq`, `ne`, `lt`, `le`, `gt`, `ge`
- [x] `not`
- [x] Short-circuiting `and`, `or`
- [x] Integer `div` and `mod` (truncating; divide-by-zero is a structured error)
- [x] `neg` and float math
- [x] Variadic `add` and `mul` (`[add, 1, 2, 3]`)

### Control flow
- [x] `if` (lazy: only the chosen branch runs)
- [x] `cond` for multi-way branches
- [x] Local bindings (`let`)
- [x] Iteration: `map`, `filter`, `fold`, `each`
- [x] A way to loop until a condition without unbounded recursion

### Functions
- [x] User-defined actors (functions) declared in YAML
- [x] Parameters and return values
- [x] Closures over the enclosing object graph
- [x] Recursion, with a stack-depth limit that fails cleanly
- [x] Documented evaluation order (and what is lazy)

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
- [ ] Anchors, aliases and non-scalar keys: decided and supported (or deliberately rejected)

---

## 3. Talking to the world

A daily driver has to do things.

- [ ] Read and write files
- [ ] Command-line arguments and exit codes
- [ ] Environment variables
- [ ] Stdin and stdout
- [ ] Run external commands and capture output
- [ ] HTTP requests
- [ ] Time and randomness, with a deterministic mode for tests and replay
- [ ] JSON, YAML and CSV parsing and emitting as library functions
- [ ] Effects are explicit in the state: a suspended run shows *which* effect it is waiting on
- [ ] Signals: external events can interrupt or resume a run (`signaled` status)

---

## 4. Errors I can actually debug

- [x] `try` / recover form, with the error available as data
- [x] Error values carry a call chain with paths and source spans
- [x] Error output is readable by a human *and* machine-parseable
- [x] Asserts, with messages
- [x] Failed runs are resumable after fixing the cause, where it makes sense — opt-in pure recovery checkpoints preserve completed root fields; unfinished calls restart (full continuation restoration remains section 2)
- [x] Nothing panics: malformed input always yields a structured error (`manage.py test` mutation sweep: truncated and line-deleted variants of every case)
- [x] `raise`: user code raises its own error with a user-chosen `kind`, message and data payload
- [x] Re-raise from a handler, preserving the original error as a `cause` chain
- [x] Match handlers on error `kind`, so an unexpected kind is not swallowed
- [x] `finally` / cleanup that runs on both success and failure (needed before effects)
- [x] Errors are values in the saved state: a caught error stays readable in `root`, and the state diff shows it appear
- [x] Validation can collect every problem in one run instead of stopping at the first

---

## 5. Reuse: programs bigger than one file

- [x] Import another Helix file
- [x] Namespacing and visibility rules
- [x] A standard library with a documented, small, stable core
- [x] Package or dependency story: repository-pinned vendored paths, with provenance/update guidance in `LANGUAGE.md`; `import_vendored` golden case and `imports` relocation/version-isolation contracts.
- [x] Circular import detection with a useful error

---

## 6. Daily tooling

The difference between a language and a language I enjoy using.

- [x] `helix run` with sensible exit codes (exists, extend as features land)
- [x] REPL
- [x] Formatter with canonical output
- [x] `helix check`: parse and static checks without running
- [x] Step/trace mode: watch evaluation move through the graph
- [x] Inspect a state file: status, current position, pending effect, summary
- [x] Watch mode: re-run on change
- [x] Editor support: JSON Schema for programs, then an LSP
- [x] Shell completion and a helpful `--help`
- [x] In-language test runner (assertions plus semantic golden state comparison in `std:test`; file discovery and byte comparison remain host duties)
- [x] Single-command install (prebuilt binary or `cargo install`)


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
- [x] Sandbox/permission model for current effects: caller-authorized import roots, embedded-only mode, transitive and symlink-escape checks; future effects must acquire capabilities
- [x] Stability promise: documented what may change and what is frozen
- [x] Language reference (started: `LANGUAGE.md`) and a tutorial that a stranger could follow
- [x] A changelog

### Testing depth and breadth

The current-runtime trust milestone is implemented. The coverage map and limits are in `tests/README.md`; suites and their contracts are discoverable with `./manage.py test --describe`. Actor scenarios are measured in `runtime/coverage/actors.json`, peak RSS and timing in `runtime/memory/report.json`, and compiled mutation sensitivity in `runtime/mutants/report.json`. Scenario counts are not compiler branch-coverage percentages or proofs of exhaustive correctness.

Treat testing as a substantial engineering responsibility. The Rust/Python size ratio is a prompt to inspect gaps, not a coverage metric or a target to pad toward. Build on the existing golden, determinism, round-trip and damaged-input checks; every new suite must be reproducible through the root `./manage.py` entrypoint, with Python orchestration and fixtures kept inside the project. Testing goals below are complete when their automated checks run and demonstrate the claimed contract; golden cases remain required for language features.

- [x] Maintain a discoverable coverage map from language contracts and runtime invariants to suites, including known gaps; measure exercised branches and failure paths rather than counting test lines
- [x] Cover every builtin and form across valid inputs, wrong arity and types, empty collections, numeric boundaries, Unicode and nested values; include precise error blame
- [x] Exercise feature interactions systematically: lexical shadowing, closures, recursion, iteration, lazy branches, memoization and error recovery composed in the same programs
- [x] Test parser and graph contracts across YAML scalar styles, nesting, duplicate keys, unsupported constructs, parent links, lexical lookup and source spans
- [x] Generate ordinary-data values and programs to check serialization preserves meaning, literal/expression distinctions and stable output across repeated save/reload cycles; verify function and machine values through named inspection and execution contracts
- [x] Add property-based and metamorphic tests for semantic laws and equivalent program transformations, respecting overflow, float behavior and documented evaluation order
- [x] Compare a bounded language subset against a small independent Python reference evaluator, and library algorithms against independent host-language oracles
- [x] Extend damaged-input testing into reproducible fuzzing of parsing, linking, evaluation and emission; retain seeds, minimize failures and promote them into regression cases
- [x] Test the tests with deliberate implementation faults: establish that semantic regressions, incorrect error locations and broken serialization make the relevant suites fail
- [x] Cover imports and module isolation with generated file trees: shared dependencies, cycles, missing or malformed files, path resolution and errors originating in imported source
- [x] Exercise the CLI as a subprocess: files versus stdin, arguments, help, exit codes, stdout/stderr contracts and failed input access
- [x] Test public library and VM contracts directly, including independent VM instances and repeated use, alongside CLI golden coverage; invoke these checks through `./manage.py`
- [x] Verify resource limits at their boundaries: deep syntax, recursion, long iteration and large graphs terminate or fail cleanly under enforced timeouts without host stack overflow
- [x] Maintain repeatable performance and memory workloads across growing input sizes; detect pathological scaling and regressions with documented environments and tolerances
- [x] Expand type and class tests across nested combinations, invalid declarations, collected violations, inheritance conflicts and deterministic provenance; connect these to section 8's algebra and proof obligations
- [x] Run fast regression suites on every change and bounded generated, fuzz and scale suites on a regular automated schedule; report seeds, reproduction commands and artifacts on failure
- [x] Keep the harness trustworthy: test discovery, golden comparison, timeout/crash detection and failure reporting; provide discoverable suite selection without creating competing entrypoints

### Obligations activated by later milestones

These require features from sections 2–3 and a versioned state format. They remain explicit obligations, not claims that disk restoration, effect replay or state migration already works. Today's immutable in-memory pause points, pure recovery and ordinary-data finished-root fixpoints are covered separately; named function and machine inspection graphs are not executable disk snapshots.

- [ ] When suspension lands, compare uninterrupted execution with save/resume at every supported boundary, including nested calls, live locals, hand-edited states and fresh-process restoration
- [ ] When effects land, use controlled adapters to test failure, retry, cleanup, permissions and interruption; prove completed effects are not accidentally repeated after resume
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
has_ssh:    [class, {ssh_port: {type: port, default: 22}}]
server:     [class, {hostname: string, packages: [list_of, string]}]
web01:      [inherit, [server, has_ssh], {hostname: web01, packages: [nginx]}]
```

### Declarative types

Audit evidence: `types_declarative`, `types_annotations`, `types_are_values`, `classes`, `class_conflicts`, `class_constraints` and `class_parametric` cover the completed declaration, annotation and class subitems below. The `golden` suite also checks emitted-root fixpoints. Broader parent boxes remain open where their diagnostic, provenance or serialization promises exceed these contracts.

A type should read like the value it describes with the specifics left open: someone who has never seen Helix can look at a record type and say what a valid value looks like. Declarations are order-independent facts, not a checking procedure, so reordering fields or splitting a declaration across maps changes nothing. Types are opt-in descriptions layered on the existing untyped core, never a reason existing programs stop running.

- [ ] A type is declared as plain YAML data in the object graph (fields, their types, defaults), not in a separate schema language
  - [ ] A declaration is an ordinary node: it has a path and span, and is found by the same lexical lookup as any value
  - [ ] Declarations are order-independent: reordering fields changes neither the type nor what it accepts
  - [x] A type may be named in a map or written inline where it is used, with the same meaning
  - [ ] A malformed declaration is a structured error blaming the declaration node, not the value later checked against it
- [x] Scalar, list, map and record types, plus optional fields, unions, enums and literal types
  - [x] Scalar types (`integer`, `float`, `string`, `boolean`, `null`) and a top type that accepts anything
  - [x] Homogeneous list and map types over an element type
  - [x] Record types: named fields, each with its own type
    - [x] Required fields: absence is a violation
    - [x] Optional fields: may be absent, checked when present
    - [x] Defaulted fields: the default is itself checked against the field's type
    - [ ] Decided and documented: are records open (extra fields allowed) or closed, and how is the other one spelled
  - [x] Unions of types
  - [x] Enums and single-value literal types
  - [ ] Types nest to any depth (a list of records of unions)
- [ ] Refinements: a type can carry a predicate (`port` is an int in 1..65535), written as an ordinary Helix expression
  - [ ] The predicate is a named or inline `fn` from the value to a boolean, run only after the base type passes
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
  - [x] Re-running an emitted `root` that contains types is a fixpoint (covered by the existing round-trip check)
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
- [x] Type constructors are ordinary functions from types (and values) to types (`[list_of, t]`, `[with_field, t, name, u]`)
  - [x] Built-in formers accept computed types as arguments, not only literal names
  - [x] A user `fn` that returns a type is usable anywhere a type is expected
  - [x] Derive a type from another: add, drop or retype a field; make every field optional
  - [x] Build a type by iteration (`map`/`fold` over a list of field names)
  - [x] A constructor given a non-type argument fails with a `type` error blaming that argument
- [x] Reflection (`types_reflection`, `types_first_class` golden cases): read a type's fields, defaults and parents as data; ask `[is, value, t]` and `[subtype, a, b]`
  - [x] `[is, value, t]`
    - [x] For scalar and collection type names and `any`; an unknown name is a `type` error
    - [x] For every declared type
  - [x] Read a record type's fields, each field's type, and its defaults as plain lists and maps
  - [x] Read a class's parents and its still-open fields
  - [x] `[subtype, a, b]`, with documented rules per type former
  - [x] A value built by reflection re-reads as the same type (reflection and declaration are inverse)
- [x] Type-time evaluation (`types_type_time`, `types_type_time_effect`; `type_time` suite): a well-defined phase that runs pure Helix to compute types, with a guarantee it performs no effects and terminates or fails cleanly
  - [x] Decided and documented: what marks type time (see Open questions)
  - [x] An effect attempted at type time is a structured error naming the effect and the node
  - [x] Bounded: the existing `depth` and `limit` caps apply, so type time ends or fails cleanly
  - [x] Deterministic: no time, randomness, environment or file contents reach type time unpinned
  - [x] The same type-time result whether reached by `helix check` or by a full run
- [x] Type-time errors (`types_type_time_error` golden case) are the same structured errors as run-time ones, with the phase recorded in the state
  - [x] Same shape and kinds (`kind`, `message`, `path`, `line`, `column`), catchable with `try` like any other
  - [x] The saved state records which phase failed
  - [x] A type-time failure stops before any run-time effect is attempted

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
  - [ ] Several parents sharing a field they agree on (including diamonds: one ancestor reached twice merges once)
  - [ ] Classes inheriting from classes, to any depth
  - [ ] The result is a plain flattened type: emitted, it reads as if written out by hand
  - [ ] Constraints from every parent all hold on the result
- [ ] Documented, deterministic merge and override rules (which parent wins, what counts as a conflict, how lists and maps combine)
  - [ ] Decided: the merge model (see Open questions) and whether inheritance is nominal, structural or both
  - [ ] Field types from two parents: when they combine and when they conflict
  - [ ] Defaults: a child's explicit value overrides a parent default; two differing parent defaults are a conflict
  - [ ] Lists and maps: one stated rule each (replace, append, or merge by key), with an explicit way to ask for another
  - [ ] Parent order never changes the result
  - [ ] The rules are written in `LANGUAGE.md`, with a golden case per rule
- [ ] Conflicts between parents are type-time errors that name both sources, never silent last-writer-wins
  - [ ] The error gives the field and the path and line of each disagreeing declaration
  - [ ] Every conflict in a declaration is reported in one pass
  - [ ] A conflict is resolved only by an explicit override in the child, and the override is visible in provenance
- [ ] An abstract class cannot be realized until every required field is filled; the error lists what is missing and which class demanded it
  - [ ] Using an abstract type where a concrete one is needed is an error, not an implicit default
  - [ ] All missing fields are listed at once, each with the class that demanded it
  - [ ] An override that violates a parent's field type or constraint blames the override and names the parent
- [ ] Provenance: for any field of a resolved type, the state shows which class or override supplied it
  - [ ] Each field records its source (class or override) as a root-relative path with line
  - [ ] Overridden values are shown too: what lost, and to what
  - [ ] Provenance is queryable as data from Helix, not only visible in the output
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
  - [ ] The plan is plain YAML: ordered steps, each naming the field of the type it realizes
  - [ ] An empty plan means the machine inhabits its type
  - [ ] Planning changes nothing on the machine (dry run is the default, applying is the explicit act)
- [ ] Applying a plan uses the effect system (section 3), is idempotent, and suspends/resumes per step (section 2) with the pending step visible in the state
  - [ ] Each step is an explicit effect recorded in the state before it runs
  - [ ] A failed or interrupted apply resumes from the pending step, not from the start
  - [ ] Applying twice changes nothing the second time
  - [ ] After applying, a fresh plan is empty
- [ ] Drift report: what the machine has that its type does not say, and the reverse
  - [ ] Both directions reported as data, per field, with the type's provenance for each
  - [ ] Decided and documented: what counts as unmanaged (ignored) versus drift
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

- `dogfood_config_diff`: added/removed/changed keys, nested value changes, null versus absence, unchanged and empty snapshots. Friction: building report records requires nested `set` calls because symbols in ordinary map values are literal data; nested changes are reported at the top-level key.

- `dogfood_project_inventory`: missing required fields, duplicate names and dangling dependencies, plus valid and empty inventories. Friction: presence checks do not validate field types; this example assumes string names and list dependencies. Schema validation remains separate work.

- `dogfood_dependency_order`: stable ordering of diamond dependencies, missing references, cycles, self-dependencies and empty graphs; independent work completes before blocked tasks are reported. Friction: immutable maps have no removal operation, so each batch rebuilds the pending map. Blocked results preserve the graph rather than claiming every blocked node belongs to a cycle.

- `dogfood_expense_reconciliation`: aggregate duplicate accounts and refunds in integer cents; report mismatched totals and accounts present on only one side, including zero balances; balanced and empty ledgers yield no findings. Friction: currency units and matching keys are conventions in user code, with no decimal-money type.

- `dogfood_work_queue`: stable priority sorting and dispatch through `std:list.sort_by`, with negative priorities, equal-priority payloads, sorted/reversed/empty/singleton inputs, independent calls, idempotence and source-order preservation. The original 100+ line example now shares its sort implementation with the standard library. Friction: insertion sort repeatedly copies lists and is suitable only for small queues.

### Errors as a first-class feature (section 4)
- [x] Parse and validate a messy inline dataset where every row may be malformed; `try` turns each failure into a record, and the report lists all of them with path and line
- [x] A calculator or expression evaluator that raises its own `syntax`/`arity` errors and recovers per input line, never aborting the batch
- [x] A retry loop: attempt a failing step, inspect the error's `kind`, retry on transient kinds, give up on the rest, and keep the attempt history in the state
- [x] Wrap a low-level error in a domain error with `cause`, then read the full chain from the saved state without a debugger
- [x] A function whose `assert` preconditions document its contract; call it wrongly and fix the caller from the assertion message alone
- [x] Turn a failed run's saved error into a regression golden case in under a minute (error, expected state, fix)
- [x] Cleanup with `finally` around a multi-step job, proven by a golden case where a middle step fails
- [x] Diff two runs of one program, one passing and one failing, and explain the failure from the state diff alone

Evidence: `dogfood_errors` and the paired `dogfood_errors_regression` / `dogfood_errors_fixed` golden cases exercise these workflows without host effects. The batch retains each failed row's index, source and full error (path, line, column and calls); valid rows after failures still finish. Calculator inputs recover independently. Retry histories cover eventual success, immediate fatal failure and exhausted transient attempts. `wrapped.cause.cause` retains the original parser failure under the domain wrapper.

The saved `wrong_call` assertion says to pass at least 1. The minimized regression keeps `count: 0`; its corrected companion changes only that input to `count: 1`. Comparing their expected states explains the failure directly: the assertion and caller spans in `error` become a finished `root` with `count: 1` and `result: 2`. To repeat the regression workflow, copy the minimized source, run `./manage.py test --suite golden --bless`, inspect its expected state, change the caller input and rerun `./manage.py test --suite golden`. No new harness is needed.

Friction: `let` bindings are lazy, so an unused assertion does not enforce a contract; these examples put assertions in the result's dependency chain. Domain wrapping must retain the original map explicitly under `cause` before re-raising. Pure cleanup has no observable resource release, so a deliberately raised cleanup error witnesses execution on both outcomes; a successful cleanup separately proves preservation of the middle-step error. These are pure workflow exercises, not claims of external retries or resource management. Parser-document coordinates appear in the message; structured spans point to the parsing actor, so row index and source are retained alongside them.

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
- [ ] Replace the hand-rolled field checks in `dogfood_project_inventory` with a declared type; the type error report matches or beats the hand-written one
- [ ] Write a function that takes a type and returns a derived type, and use it to remove real duplication between two declarations
- [ ] Model my own machines as types: a laptop and a server sharing abstract classes, differing only in what they override
- [ ] Introduce a deliberate conflict between two parent classes and fix it from the type-time error alone
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
- [ ] Collection and text algorithms: sorting, grouping, deduplication, map merge/removal, splitting and joining as imported Helix functions over the existing collection primitives. Reuse the algorithms in the dogfooding cases; document copying costs and iteration limits before treating them as general-purpose library functions.
  - [x] Stable grouping by a string-valued callback in `std:group.by`, covered by `std_group`; preserves first key occurrence and item order, with documented copying costs and iteration limits. Real tooling caller: `./manage.py inventory` invokes `tooling/inventory.yaml` to group repository files by directory and saves replayable input/state; covered by `dogfood_tooling_inventory` and CLI contracts.
  - [x] Stable comparator sort (`sort_by`) extracted from the work-queue example, plus first-occurrence deduplication (`unique`), in `std:list`; covered by `std_list_algorithms` and `dogfood_work_queue`. Both use quadratic scans/copying and runtime iteration limits; intended for small inputs.
  - [x] `std:list` access/slicing, enumerate/zip, one-level flatten/flat-map, short-circuit searches, quantifiers, count/partition, grouping/frequencies and default sorting; contracts and boundary cases live in `std_list_helpers`.
  - [x] Shallow right-biased merge and single-key removal in `std:map`, covered by `std_map`: stable key order, missing/null distinction, nested replacement, empty and Unicode keys, and checked arguments. Immutable copies can cost quadratic time/allocations; runtime iteration limits apply.
- [ ] Configuration diff, dependency ordering and validation reports as reusable Helix modules, extracted from the existing dogfooding cases. Their policies and error recovery belong in Helix; primitive lookup, equality and structured error attribution remain runtime services.
- [ ] Type descriptor construction and transformation: build list/map/record/union/optional/default descriptions and derive fields with Helix functions over plain maps and lists. Replace the corresponding policy in `src/builtins/typing/construct.rs` once type-value validation and call compatibility are preserved; checking defaults depends on the checker below.
- [ ] Drift and convergence planning: replace the pure comparison and plan construction in `src/builtins/typing/machine.rs` with Helix modules, using desired and observed maps as inputs. Preserve missing versus null, stable step order, extra-field policy and empty plans for conforming inputs. Observation and application stay separate effects.

### Next: types and callable values make the larger pieces practical

The working tree contains Rust type, class and callable-value work beyond the established pure core. These are migration candidates, not claims that the complete section 8 contract already works.

- [ ] Type validation and checking: move descriptor traversal, nested violation collection, unions, records and refinement orchestration from `src/builtins/typing/ty.rs` into Helix. Needs dependable primitive type inspection and first-class predicate calls; preserve declaration errors versus rejected values versus failed predicates, bounded traversal, deterministic diagnostics and source blame. Keep one authoritative checker used by both annotations and explicit checks.
- [ ] Class composition and realization: implement inheritance conflict detection, defaults, required-field checks, flattening and provenance from `src/builtins/typing/class.rs` in Helix, building on the shared checker and the supported subtype rules. Requires callable predicates and sufficient provenance reflection; preserve explicit conflict resolution and parent-order independence. Future algebra and subtyping policy should live beside this code rather than grow another Rust policy engine.
- [ ] Machine vocabulary and planning policy: declare packages/files/services/users, fleet constraints, dependency ordering and plan validation in Helix (section 8). Given typed observations, compute the complete plan as data; host adapters expose narrowly defined effects. Resume and retry policy can follow once pending effects and continuations serialize (section 2).

### With a few explicit runtime services

- [ ] Canonical formatting and state inspection: implement layout, state summaries and eventually migrations in Helix. Replacing `src/emit.rs` needs lossless scalar/source reflection, scalar-to-text and escaping support, plus a bootstrap output path that does not recursively invoke the formatter. Preserve quoted literals, expression heads, source spans and the existing round-trip fixpoint.
- [ ] Static checks as Helix graph passes: dependency/cycle diagnostics, annotation discovery and type reports for `helix check`. Requires access to the unevaluated graph, parent links, paths and spans without triggering effects; reuse the authoritative checker. Runtime cycle guards and resource limits remain enforced by the bootstrap.
- [ ] Test and task orchestration: express case discovery, golden comparison, round-trip checks, mutation generation and build/lint invocation in Helix once file, process, argument and parsing effects exist (section 3). Keep `./manage.py` as the obvious root entrypoint and bootstrap launcher; replace its orchestration behind that interface rather than create a competing workflow. Host-level crash checks must still detect a broken interpreter.
  - [x] Pure test policy in `std:test`: assertions, expected errors, per-case recovery, table/state fixtures, bounded properties, differential checks and serializable reports. `dogfood_testing` composes selection, failure reruns, fixture factories, shrinking and nested suites; `std_test_failure` proves a failed report becomes a structured VM error. Host filesystem/process orchestration remains pending.

### Migration bar

Move one complete responsibility at a time: a Helix module becomes the authoritative implementation, production callers use it, and the replaced Rust policy is removed. A forwarding wrapper around the same Rust implementation does not count. Preserve public syntax, structured errors, locations and state round trips; prove parity through `./manage.py test` golden cases, malformed-input coverage where applicable, and realistic size checks. A temporary comparison against the old implementation can establish parity, but must not leave two policy engines to maintain. Defer a self-interpreter or YAML parser rewrite until lossless graph reflection, explicit continuations and bootstrap recovery make it a concrete improvement.

---

## Open questions

Decide these before the features that depend on them.

- [x] How are user-defined actors declared in YAML so they stay readable? `name: [fn, [params], body]`; called as `[name, args...]`
- [ ] What is the evaluation model for effects: pull, push, or explicit yield?
- [ ] Does a suspended state embed the source, reference it, or both?
- [x] Types are opt-in outside configs: unannotated code retains its behavior; `expect` and function parameter/result contracts enforce declared types (`types_annotations`; `LANGUAGE.md`).
- [ ] Is inheritance nominal (declared parents) or structural (anything with the right fields satisfies a class), or both?
- [x] Type time is an explicit `[type_time, expression]` region, with `phase: type` on failures; `check` collects diagnostics with the same evaluator (see `LANGUAGE.md`).
- [ ] How do parent classes merge: Nix-module-style priorities and merge functions, CUE-style unification, or linearized inheritance?
- [ ] What is the story for secrets in persisted state?
- [ ] Which parts of `flintwinters/helixcpp` are worth carrying over, and which are better left behind?
