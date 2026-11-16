# Helix Charter Cockpit

**Goal:** Helix is a language I can reach for every day, for real scripts and automations, without apologizing for it.

The test for "daily driver" is simple: I start a task in Helix instead of Python or shell, I finish it, and I can open the saved state afterwards and understand exactly what happened.

This charter records direction, acceptance criteria and bounded completion evidence.
[AGENTS.md](AGENTS.md) owns project architecture and current priorities;
[LANGUAGE.md](LANGUAGE.md), [LIBRARY.md](LIBRARY.md) and [HOST.md](HOST.md)
own implemented contracts. [tests/README.md](tests/README.md) owns test operation.

**Completion rule:** check an item only when its stated scope works end to end
and has automated evidence through `./manage.py`. Language features also need a
golden case in `tests/cases/`; host behavior needs independent host observations.
A demo or a `tests/programs/` fixture alone does not qualify. Parents stay open
while any stated obligation remains open. Tests do not establish formal proofs
or real-world adoption; those milestones need their own evidence.

**Direction:** declarative machine configuration through first-class types.
Resolve and validate desired configuration purely, then observe, review and apply
through caller-authorized effects. Daily scripting and readable saved execution
must make that foundation useful in practice.

**Audited status (2026-11-13):** the core language, bounded reversible machines,
current host adapters, CLI/editor tooling and runtime trust contracts are
implemented. Versioned snapshots restore execution; caller-owned journals refuse
uncertain effect replay. The Docker nginx workload is qualified locally and over
SSH, including controller-death recovery. Opaque host-only secret inputs,
read-only recovery inspection, shared-domain target locks and pure class-driven
nginx configuration have bounded acceptance evidence. These do not establish
general machine application, secret-safe arbitrary data, remote fencing, fleet
recovery or independently distributed reversibility.

**Current focus:** improve measured development feedback on production policy
workloads and preserve the completed nginx contract. The next operational
expansion must name a concrete need and declare its supported migration scope.
Sections 8–11 retain deferred possibilities and unresolved acceptance criteria;
none is an automatic next task. Building a UI is out of scope; execution remains
accessible through APIs and CLI tooling.

**Admission rule:** section 12 governs every addition and policy migration.
Complete useful workloads and simplify existing semantics before opening another
feature family. Completed audits do not retire these continuing rules.

---

## 0. Foundation — complete

YAML representation graphs retain parent links, source spans and root-relative paths. Explicit `!call` and `!ref` tags (equivalently `$call` and `$ref` singleton maps) use lexical lookup; `$literal` protects arbitrary data. Scalar style and mapping presentation order do not affect execution; execution detects cycles and emits structured diagnostics. Finished ordinary-data roots round-trip through YAML. `./manage.py` owns golden testing and structural linting through the hyperlinter submodule.

## 1. Core language — complete

- Values: strings with concatenation/length/slicing, integers, floats, booleans, null, lists and maps; explicit type rules without silent coercion (`LANGUAGE.md`).
- Operators: checked integer arithmetic, float math, variadic `add`/`mul`, truncating `div`/`mod`, comparisons and short-circuit boolean operations.
- Control: lazy `if`/`cond`, `let`, collection iteration and bounded looping without recursive calls.
- Functions: YAML declarations, parameters/results, lexical closures and recursion with a clean depth-limit error; evaluation order and laziness are documented.

---

## 2. The thing that makes Helix Helix: state you can read and resume — complete within bounded runtime contracts

If these are not great, there is no reason to use Helix over anything else.

Reversibility is a promise type. A machine that mixes in this promise must expose all of its behavior and complete execution state so it can always be serialized and restored. This includes stack, heap, local bindings, continuations, iteration progress and pending effects wherever they determine execution; hidden host state cannot carry part of the continuation. Suspend/resume follows from satisfying this promise. The runtime enforces this through named, composable `promise` types and immutable `mixin` application. The initial registered obligation is `reversible`: admission and subsequent transitions preserve bounded executable serialization, refusing a transition before losing the previous continuation. Promised machines refuse ordinary host dispatch and arbitrary effect replies. `PromisedSystem` composes promised participants through atomic `peer_send`/`peer_receive` rendezvous, retaining both continuations, exact delivery receipts and scheduler position in one checkpoint. The complete system is the restoration boundary; independently hosted remote participants and effects outside it remain separate work. This is state restoration, not external rollback or secret-safe serialization.

### Machine dependency closure and portability

Dependency closure is an explicit machine contract: distinguish captured execution
state from the external environment needed by future effects. Immutable storage
may be shared between retained versions; exporting a machine must materialize all
reachable execution dependencies, including code, values, captures and
continuations, without relying on the original process. A compatible runtime is
still required. Snapshot validation checks encoded structure; completeness also
requires implementation review and restoration acceptance evidence.

Adopt these dependency boundaries as the design direction, not as implemented
machine classifications:

- **Closed:** execution depends only on captured code and input data.
- **Explicitly bound:** execution additionally requires declared external resources;
  the destination supplies their bindings and fresh permissions. Requirements
  carry no authority, and binding a resource does not reproduce its contents or
  guarantee identical future observations.
- **Open:** execution can discover further dependencies, including through arbitrary
  host commands. Command grants authorize operations but cannot enumerate every
  file, subprocess or service those operations might use. Declarations alone do
  not establish closure.

Classification must follow reachable capabilities and enforced boundaries, rather
than an author's assertion. Retaining completed effect outcomes preserves recorded
computation; it does not capture the environment of future effects. Snapshot
restoration alone therefore establishes neither environmental reproducibility nor
external rollback. Capturing an operating environment is a separate contract.

- [x] Establish the initial pure portability workload: move a suspended machine to
  another directory and finish in a fresh process after deleting original source
  and imports, with only its checkpoint and compatible runtime (`snapshots`,
  section 9's source-independent continuation acceptance).
- [ ] Derive and enforce dependency classifications for a concrete workload before
  claiming closed or explicitly bound portability.
- [ ] Expose an inspection report of captured inputs, pending external requirements
  and open boundaries; reuse snapshot and capability representations rather than
  maintaining an independent dependency inventory. Incomplete knowledge must remain
  explicit, including dependencies discovered only by future commands.

Design decisions beyond that implemented boundary:

- Edits are externally supplied state changes: from the VM's perspective they
  simply appeared, like cosmic rays. Continue from the supplied state without
  invalidating or recomputing completed work to make it agree with edited source.
  Retained provenance describes recorded execution; it does not certify that an
  edited value was produced by that execution. Structural snapshot validation
  still applies; this decision does not promise acceptance of malformed states.
- Use monadic composition as the direction for independently hosted reversible
  computations. The concrete representation, sequencing laws and transport/
  restoration protocol still need a workload and acceptance evidence. Monadic
  composition alone does not establish agreement about already-delivered messages
  when peers restore independently; the current joint boundary remains in force.

- [x] A reversibility promise type enforces complete behavioral and state exposure on machines that mix it in, within the documented snapshot budgets; composed promises participate in membership, annotations and subtyping (`library`, `semantics`, `promises` golden)
- [x] Promised participants communicate within a joint reversible system: receipt histories correspond exactly to completed peer effects, duplicate deliveries are idempotent, and failed deliveries preserve both previous participants. Fresh-process restoration covers request acceptance before reply; omitted receipts and mismatched participant histories are rejected (`library`, `snapshots`). This does not establish distributed rollback or remote fencing.
- [x] Serialization captures the whole promised machine, including nested calls, live allocations and attached obligations, without dependence on the original process (`library`, `snapshots`); unsupported growth leaves a serializable refusal state
- [x] **Suspend/resume:** explicit `snapshot`/`restore` save and continue complete bounded execution in fresh processes; version 2 uses canonical JSON (a YAML subset), separate from inspection and pure recovery (`snapshots`, public VM boundary tests)
- [x] Execution position persisted as source-qualified root-relative paths; embedded graph topology validates references, and object-table names preserve sharing without exposing addresses (`snapshots`)
- [x] All five statuses round-trip: `ready`, `running`, `finished`, `error`, `signaled`; public snapshot tests cover intermediate, failed, pending-effect and interrupted machines
- [x] Edit a suspended live parameter and resume a different result (`snapshots`). Completed memoized work remains completed; source edits are not requests for dependency invalidation
- [x] Pure continuation replay: the same saved state produces the same result and annotation evidence. Future effect replies are explicit external inputs; this does not promise identical external execution (`library`, `snapshots`)
- [x] Latest retained evaluated-value evidence includes producing source paths and spans, including local computed results; bounded ordinary-data summaries persist through snapshots and explicitly report truncation (`library`, `memory`). This is not an unbounded per-invocation trace
- [x] Canonical key order, byte-stable zero-step restoration, localized live-parameter edits, and a bounded nested-call transition diff with unchanged embedded source objects are qualified (`snapshots`). Object names preserve sharing within a checkpoint; they are not persistent global identities across arbitrary graph changes
- [x] Versioned execution format with compatibility fixtures and explicit rejection of unsupported format/runtime versions; version 1 migrates explicitly to version 2; future incompatible changes require a migration or documented rejection (`LANGUAGE.md#executable-disk-snapshots`)
- [x] YAML representation boundaries decided: bounded acyclic aliases and standard tags are supported; cyclic aliases, unknown custom tags and non-string keys are rejected (`yaml_model` golden and conformance suite).

---

## 3. Talking to the world — complete within supported adapter bounds

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
installed `curl`. VM signals are explicit host event APIs; the command host maps OS signals to its documented cancellation policy.
Suspension and interruption preserve live continuations in memory; emitted
inspection state is not a restorable disk snapshot. Retained branches require
explicit host handling and do not share a global effect deduplication service.
Executable snapshot restoration has its own versioned API. Caller-owned journals preserve completed effects and require explicit reconciliation of uncertain requests; distributed recovery and effectful reversibility beyond section 2's bounded promise remain open.

---

## 4. Errors I can actually debug — complete

Structured errors are readable data with paths, spans, call chains and retained causes. `try`, kind-matched recovery, `raise`, assertions and `finally` support independent validation and cleanup on either outcome. Caught errors remain visible in saved roots. Malformed-input mutation sweeps check structured failures rather than panics.

Pure recovery checkpoints preserve completed root fields and restart unfinished calls after correction. Executable continuation restoration uses the separate versioned format in section 2.

## 5. Reuse — complete

File imports provide isolated namespaces, documented visibility and circular-import diagnostics. The standard library has a documented core. Dependencies use repository-pinned vendored paths with provenance/update guidance in `LANGUAGE.md`; `import_vendored` and the `imports` suite cover relocation and version isolation.

---

## 6. Daily tooling — complete within current CLI/editor scope

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

## 7. Trust — complete within current runtime bounds

- [x] Performance: a 10k-node program runs in well under a second
- [x] Memory stays bounded on long runs: shared work/data budgets and value/graph/depth caps; `memory` checks constant-state iteration and scaling
- [x] Fuzzing of the parser and evaluator
- [x] Sandbox/permission model for current effects: caller-authorized import/file roots, environment names, executables and HTTP origins, plus stream/clock/random grants; transitive and symlink-escape checks. These are capability boundaries, not OS isolation.
- [x] Stability promise: documented what may change and what is frozen
- [x] Language reference (started: `LANGUAGE.md`) and a tutorial that a stranger could follow
- [x] A broad strokes [changelog](CHANGELOG.md) records major capabilities, migrations and limitations; Git history supplies detail rather than replacing it

### Testing depth and breadth

The current-runtime trust/documentation and bounded persistence obligations below are implemented and tested. New formats, adapters and platforms must extend that evidence before claiming support. The coverage map and limits are in `tests/README.md`; suites and their contracts are discoverable with `./manage.py test --describe`. Actor scenarios are measured in `runtime/coverage/actors.json`, peak RSS and timing in `runtime/memory/report.json`, and compiled mutation sensitivity in `runtime/mutants/report.json`. Scenario counts are not compiler branch-coverage percentages or proofs of exhaustive correctness.

Treat testing as a substantial engineering responsibility. The Rust/Python size ratio is a prompt to inspect gaps, not a coverage metric or a target to pad toward. Build on the existing golden, determinism, round-trip and damaged-input checks; every new suite must be reproducible through the root `./manage.py` entrypoint, with Python orchestration and fixtures kept inside the project. Completion requires automated checks demonstrating the claimed contract; golden cases remain required for language features.

Completed coverage spans:

- Builtins, forms and feature interactions; arity/types, empty inputs, numeric boundaries, Unicode and precise blame.
- Parser/source graphs, imports/module isolation, ordinary-data fixpoints and function/machine inspection contracts.
- Generated properties, metamorphic laws, independent reference oracles, reproducible fuzzing and deliberate implementation mutations.
- CLI subprocesses and public VM APIs; resource boundaries, stack safety, performance/memory scaling and type/class algebra.
- Harness discovery, comparison, timeout/crash detection and actionable failure artifacts; fast regressions plus scheduled bounded generated/fuzz/scale runs.

### Persistence obligations and evidence

Versioned execution snapshots now activate the restoration and compatibility obligations. Caller-owned effect journals now add crash/uncertainty acceptance; distributed recovery and effectful reversibility beyond section 2's bounded promise remain open. Immutable pause points, pure source recovery and ordinary-data fixpoints retain separate contracts; inspection graphs remain non-restorable.

- [x] Compare uninterrupted execution with save/resume at every transition of bounded nested-call, iteration, annotation, refinement, handler and child-machine programs; fresh CLI processes additionally cover live-local edits, embedded imports and representative existing golden workloads (`library`, `snapshots`, `execution`)
- [x] Fresh-process effect journals retain completed outcomes, require fresh grants and refuse uncertain replay after controller death. Exact-request reconciliation records independently observed outcomes; real command/counter tests prove completed work is not repeated (`durable_effects`), while `effects` and `long_commands` retain shared adapter failure/retry/cancellation coverage. This does not establish exactly-once external execution or distributed recovery
- [x] Keep committed version-1 ready/error snapshots and exact public error expectations; test their explicit migration to version 2 and unsupported-version rejection. Future version changes must extend these fixtures (`snapshots`)

---

## 8. Types: declarative, first-class, and the machine-config niche

The long-range target. A type is ordinary readable YAML, a value like any other, and a machine config is a type rather than a script: everything about it is known and checked at type time, before any effect touches a machine.

Principles that hold for the whole section, and that settle most design questions inside it:

- **One language, one graph.** Types live in the same object graph as programs and state, are written in the same YAML, and are computed by the same evaluator. There is no schema dialect, template layer or generics syntax beside the language. If a feature needs a second notation, the design is wrong.
- **Readable types and explicit persistence boundaries.** Ordinary type data can be read, diffed, edited and re-read. Types containing predicates retain executable captures only through versioned snapshots; emitted inspection graphs are not executable source. Saved annotation evidence is bounded and may be explicitly truncated.
- **A type describes, it does not act.** A type says what a value is. It never converts, coerces, fetches or repairs; checking a value against a type either passes or yields error data. This matches the no-silent-coercion rule in `LANGUAGE.md`.
- **Wrong is loud and early.** The reason for types here is to move failures from "halfway through changing a machine" to "before anything ran", and to report all of them at once, each blamed on a node with path and line.
- **Explain where things came from.** In Nix and Ansible the hard question is rarely "what is this value" but "who set it and why did it win". Helix answers that from the state, not from a debugger or a precedence table held in the reader's head.

The sketch below shows the intended feel only; the syntax is undecided (see Open questions) and must not be treated as a spec.

```yaml
has_ssh: {$call: [class, {ssh_port: {type: port, default: 22}}]}
server: {$call: [class, {hostname: string, packages: {$call: [list_of, {$ref: string}]}}]}
web01: {$call: [inherit, {$call: [server, {$ref: has_ssh}]}, {hostname: web01, packages: [nginx]}]}
```

### Declarative types — complete within the documented fragment

Completion decisions: this milestone covers ordinary type data, source-preserving annotation inspection, bounded non-executing checking, and collection of independent reached failures. These settle the earlier blanket promises explicitly: computed types emit their evaluated descriptors, with the written annotation expression preserved separately; plain `check` reports unsupported computations as unchecked rather than trying every terminating type-time expression. Versioned execution snapshots now restore predicate captures and checked annotation state, with boundary tests in `library` and fresh-process tests in `snapshots`. The stronger serialization proof obligation below remains open. Inspection is not executable restoration.

Evidence: the `type_algebra` suite owns declaration/default, violation, argument/predicate blame and annotation-evidence regressions; `static` covers source-wide obligations and damaged annotated programs; `execution` and `errors` cover retained branches and source-based recovery. The existing declarative, annotation, refinement and class golden cases preserve readable contracts, and `golden` checks ordinary emitted-root fixpoints.

A type should read like the value it describes with the specifics left open: someone who has never seen Helix can look at a record type and say what a valid value looks like. Declarations are order-independent facts, not a checking procedure, so reordering fields or splitting a declaration across maps changes nothing. Types are opt-in descriptions layered on the existing untyped core, never a reason existing programs stop running.

- [x] A type is declared as plain YAML data in the object graph (fields, their types, defaults), not in a separate schema language
  - [x] A declaration is an ordinary source node: it has a path and span, and is found by the same lexical lookup as any value (`types_declarative`, `types_first_class_errors`; retaining locations on evaluated values remains separate)
  - [x] Declarations are order-independent: reordering record fields changes neither the type nor what it accepts (`types_derivation.round_trip` reconstructs fields in canonical order; the shared checker consumes the resulting field map)
  - [x] A type may be named in a map or written inline where it is used, with the same meaning
  - [x] A malformed consumed declaration is a structured error blaming its declaration expression, not the value checked against it; lexically resolvable annotation references resolve back to their declarations. Raw defaults are checked even when overridden or nested beneath empty collections; structural `subtype` remains a non-validating proof query
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
- [x] Refinements: a type can carry a predicate (`port` is an int in 1..65535), written as an ordinary Helix expression
  - [x] The predicate is a named or inline `fn` from the value to a boolean, run only after the base type passes (`types_declarative`, `types_first_class`; the shared checker gates predicates on base membership)
  - [x] A predicate that raises or returns a non-boolean produces a distinct `predicate` error at the predicate source; raised errors retain their original cause. False remains ordinary rejection, and reached union predicate failures are not discarded
  - [x] A rejected value's violation identifies its named or inline predicate by source-qualified path and line, so the report identifies the rule that failed
  - [x] Refinements stack (a refinement of a refinement) and compose with every other implemented type former (`types_refinement_composition`: base gating, collection elements, optional/defaulted fields, unions, enums, literals and class fields; class membership additionally enforces fixed values and constraints, `types_class_membership`)
- [x] Annotate a value or a `fn` parameter/result with a type; a mismatch is a structured `type` error blaming the offending node with path and line
  - [x] Annotate a value in a map; the annotated value is still read by other expressions as the plain value
  - [x] Annotate `fn` parameters; direct bad arguments carry their individual call-site paths/spans and the primary error blames the first offending argument. Synthesized callback arguments use the callback site without inventing source operands
  - [x] Annotate a `fn` result; a bad result blames the body, with the call recorded
  - [x] Unannotated ordinary values retain their evaluation semantics; executions with no annotation checks add no annotation metadata. Regression coverage and paired annotated/unannotated programs verify this contract, rather than asserting unverified byte identity with every historical pre-types output
  - [x] The error carries a readable expected-type label, original actual value and the path *inside* the value where they diverge; explicit presence distinguishes a missing field from null
- [x] Type checking collects every violation in one pass instead of stopping at the first
  - [x] A violation is data: path into the value, expected, actual, actual presence, and the rule that rejected it; malformed defaults additionally identify their declaration path
  - [x] All violations of one value against one valid type declaration are returned together, nested ones included; malformed defaults stop candidate checking
  - [x] Violations come out in a stable, documented order so reports diff cleanly (`types_violation_order`; depth-first field/key/index order, record extras last, as documented in `LANGUAGE.md`)
  - [x] `check --evaluate` reports independent reached annotation failures in one run, deduplicating shared failures; plain `check` visits written annotations in uncalled bodies too. Neither promises to execute every branch or invocation
- [x] Ordinary type data round-trips; saved inspection retains bounded evidence of the annotations governing values
  - [x] Data-only evaluated type descriptors remain ordinary root data. Annotation evidence preserves the written declaration expression and source reference separately from its evaluated type; it does not replace values with executable source
  - [x] Re-running an emitted ordinary-data `root` under `$literal` is a data fixpoint, including data-only type descriptions (covered by the existing round-trip check; types containing predicate functions use non-restorable inspection graphs)
  - [x] Successful `expect`, parameter and result checks survive as immutable annotation evidence. Ordinary data is exact; live functions/machines use marked shallow summaries, and budget exhaustion explicitly marks a truncated history. Re-reading a literal root does not restore annotations or executable captures
- [x] `helix check` type-checks a program without running it or performing effects
  - [x] Checks annotations in the documented bounded static fragment, including ordinary YAML descriptors and unused function declarations; unresolved computations, default validation and predicates remain explicitly unchecked, never passed by assumption
  - [x] Preserves the established versioned static report: `mode: static`, `status`, `diagnostics`, and `unchecked`, with exit codes 0/1/2. Evaluation collection retains its separate state-document contract
  - [x] Covered by the maintained mutation sweep: damaged annotated programs yield a well-formed static report, never a panic

### First-class types — complete within current runtime bounds

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
- [x] Reflection (`types_reflection`, `types_first_class`, `types_reflection_inverse` golden cases): read a type's fields, defaults and parents as data; ask `{$call: [is, {$ref: value}, {$ref: t}]}` and `{$call: [subtype, {$ref: a}, {$ref: b}]}`
  - [x] `{$call: [is, {$ref: value}, {$ref: t}]}`
    - [x] For scalar and collection type names and `any`; an unknown name is a `type` error
    - [x] For every declared type, including classes: field schemas, explicitly fixed values and whole-record constraints (`types_class_membership`; defaults realization filled in are not pinned)
  - [x] Read a record type's fields, each field's type, and its defaults as plain lists and maps
  - [x] Read a class's parents and its still-open fields
  - [x] `{$call: [subtype, {$ref: a}, {$ref: b}]}`, with documented rules per type former
  - [x] A value built by reflection re-reads as the same type: `from_fields` for records; `std:types.redeclare(class, parents)` recovers a class from provenance, fixed values and constraints beyond its parents' (`types_reflection_inverse`; predicate functions are values within a run, but their emitted YAML is a non-restorable inspection graph)
- [x] Type-time evaluation (`types_type_time`, `types_type_time_effect`, `types_type_time_preflight`; `type_time` suite): a well-defined phase that runs pure Helix to compute types, with a guarantee it performs no effects and terminates or fails cleanly
  - [x] Decided and documented: what marks type time (see Open questions)
  - [x] An effect attempted at type time is a structured error naming the effect and the node
  - [x] Bounded: the existing `depth` and `limit` caps apply, so type time ends or fails cleanly
  - [x] Deterministic: a region's result is a function of the complete linked source graph and the run-time values it reads. Effect requests are blocked inside the phase and its dependencies are recomputed purely, so the only run-time data that can enter is a parameter or captured value from a frame older than the region. An outermost region records each such value (functions by source node) in the machine; the state's `type_time_inputs` names them and inspection shows the values. A region that reads none is closed and leaves no entry; preflighted regions are always closed (`tests/execution.rs`)
  - [x] The same type-time result whether reached by `helix check --evaluate` or by a full run (`type_time` suite); plain `check` does not evaluate type-time regions
- [x] Type-time errors (`types_type_time_error` golden case) are the same structured errors as run-time ones, with the phase recorded in the state
  - [x] Same shape and kinds (`kind`, `message`, `path`, `line`, `column`), catchable with `try` like any other
  - [x] The saved state records which phase failed
  - [x] A type-time failure stops before any run-time effect when its region sits anywhere outside a `try` operand, a `vm` thunk or an import and is not parameter-dependent: mapping entries, sequence items, call and iteration arguments, `let` bindings and bodies, every branch of `if`/`cond`/`and`/`or`, and the body of any function the program reaches when the region reads no parameter. Those regions run before the first run-time step, even in an unchosen branch or an unreferenced binding (`types_type_time_preflight`, `types_type_time_preflight_forms`, `types_type_time_preflight_dead_code`, `types_type_time_preflight_function_closed`). Regions in a `try`, a `vm` thunk, an unreached function or reading a parameter keep ordinary order so caught failures behave as documented (`types_type_time_preflight_caught`, `types_type_time_preflight_function_open`)

### Expanding the type algebra

Grow a coherent algebra of descriptions, with a stated meaning for each operation before choosing its syntax. Membership, subtyping and equivalence are different questions: checking one value must not be mistaken for proving a relationship between types. Every new former must compose with existing types, reflection, diagnostics and the YAML round trip.

- [ ] Complete the value-set algebra
  - [x] Define types by the values they admit, with explicit top (`any`) and bottom (`never`, no inhabitants), distinct from the literal `null` type (`types_bottom` golden case)
  - [x] Add intersections alongside unions; document their interaction with refinements, optional fields and open/closed records (`intersection`; `types_intersection_tuple`; `LANGUAGE.md`)
  - [x] Define union as least upper bound and intersection as greatest lower bound wherever the supported fragment can represent them; specify approximations explicitly (exact rules for `T <: intersection` and `union <: T`; sound-only `intersection <: T`)
  - [ ] Explore difference, complement and negation only within a defined universe and documented decidable fragment
- [ ] Extend structured collection types
  - [x] Fixed-length tuples and heterogeneous positions (`tuple_of`; `types_intersection_tuple`)
  - [x] Typed map keys (`map_of(V, K)`; `types_intersection_tuple`)
  - [ ] Record row constraints and explicit width/depth subtyping rules
- [x] Add function-signature types beyond coarse `function` membership and per-call annotations (`types_function_signatures`, `types_function_purity`)
  - [x] Describe parameter and result types, including higher-order functions (`function_of`; `types_function_signatures`)
  - [x] Define parameter/result variance and callable compatibility: parameters contravariant, result covariant, same arity; membership evaluates a function's declared annotations without calling it, and unannotated results promise only `any`
  - [x] Express effect contracts: `pure_function_of` demands source-provable freedom from `effect`/`step`, with callee parameters promised pure by their own annotations (`types_function_purity`)
  - [x] Check statically that code reachable from a `type_time` region meets the purity contract before it runs (a definite `effect` is a `type` error; calls the scan cannot see through are listed `unchecked`)
- [x] Support named recursive types
  - [x] Define guardedness, unfolding, equality and termination of checking (`rec`/`var`; `types_recursive`; `LANGUAGE.md`)
  - [x] Distinguish recursive descriptions from erroneous evaluation cycles (recursion is an explicit `rec` binder in the descriptor; a self-referential binding remains a cycle error)
- [ ] Make type relationships and equivalence explicit
  - [x] Define the subtyping order and semantic equivalence separately from structural declaration `eq` (`subtype`, `equivalent` as mutual subtyping, `eq` unchanged; `type_laws`)
  - [ ] Specify refinement implication and constraint composition: which facts are decidable, require evidence or remain unknown
  - [ ] Normalize types without losing original declarations or provenance; state when normalization is canonical and preserves meaning
- [ ] Connect inheritance to the algebra
  - [x] Combine inherited constraints by intersection (`class_constraint_intersection`; `LANGUAGE.md`)
  - [x] State default selection and explicit override rules separately from membership (`class_defaults`; realization fills defaults, explicit child values override them, and structural membership neither fills nor demands the default value; `LANGUAGE.md#classes-machines-and-drift`)
- [ ] Integrate every new former with the existing type system
  - [ ] Document membership, subtyping, equivalence, reflection, serialization and failure behavior
  - [ ] Cover positive, negative and boundary behavior with golden cases

### Static checking and inference

Current annotations and refinements enforce contracts during evaluation. `type_time` explicitly evaluates pure code; it does not infer types or prove unexecuted code safe. The non-executing `check` command provides conservative diagnostics, while `check --evaluate` collects evaluation failures. The goals below extend those boundaries; the formal guarantees belong under "Strengthening and proving the type system."

- [ ] Define a statically checkable fragment and the guarantees a successful check provides
  - [x] Check annotated function bodies without calling them (a small literal fragment: scalars, annotated parameters, arithmetic, comparison, `if`, calls of named functions; definite mismatches are errors, possible ones `unchecked`)
  - [x] Check both conditional branches in the supported static fragment without executing effects (`static` body and source-traversal regressions); unsupported value reasoning remains unchecked
  - [x] Report unresolved dynamic or value-dependent obligations as unchecked rather than passed (`static`, declarative annotation and production-policy regressions)
- [ ] Decide the scope of type inference
  - [ ] Infer supported expression and binding types using the authoritative type algebra
  - [ ] Specify where annotations are required and how inferred types interact with unannotated code
- [ ] Connect static results to runtime contracts
  - [ ] Reuse the authoritative membership and subtype rules instead of maintaining competing semantics
  - [x] Retain runtime checks for obligations outside the supported static fragment (`type_algebra`, annotated golden cases); a clean static report is not proof of successful execution
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
- [ ] Prove type serialization/reflection preserve meaning and that checked annotations retain their meaning after save/reload; include versioned snapshots of suspended execution and captured predicates
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
  - [x] Classes inheriting from classes, to any depth (`class_inheritance_depth`: five-level chain; flattened fields, values and per-field provenance; resource bounds still apply)
  - [ ] The result is a plain flattened type: emitted, it reads as if written out by hand
  - [x] Constraints from every parent all hold on the realized result (`class_constraints`; abstract composition defers whole-record checks until realization)
- [x] Documented, deterministic merge and override rules within the implemented class algebra (agreement, explicit resolution and whole-value collection replacement)
  - [x] Structural membership checks fields, fixed values and constraints; declared parents drive composition and provenance, not nominal instance identity. Merge rules require agreement or explicit child resolution (`types_class_membership`, `class_conflicts`; `LANGUAGE.md#classes-machines-and-drift`)
  - [x] Field types from two parents: when they combine and when they conflict (`class_conflicts`; declarations must agree or the child must explicitly narrow every parent)
  - [x] Defaults: a child's explicit value overrides a parent default; two differing parent defaults are a conflict (`class_defaults` covers inheritance, override, conflict and explicit declaration resolution)
  - [x] Lists and maps: one stated rule each (replace, append, or merge by key), with an explicit way to ask for another (`class_collection_overrides`: whole-value replacement, conflicts, empty overrides, explicit `concat` and `std:map.merge`; `LANGUAGE.md#classes-machines-and-drift`)
  - [x] Parent order never changes the result (`class_inheritance_depth`: fields, values and provenance equal under reversed parent lists)
  - [x] The implemented rules are documented in `LANGUAGE.md#classes-machines-and-drift`, with `class_defaults`, `class_conflicts` and `class_collection_overrides` golden coverage plus `class_merge_identity` suite regressions
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

Take the Smalltalk intuition of sending a message to a value, with a mathematically small, pure core: an object is readable data with behavior, and a message application is a function of the receiver and arguments. Prefer composing small capabilities to growing class hierarchies. These are deferred workload ideas, not a commitment to add object syntax or dispatch. First demonstrate where ordinary maps, explicit receiver arguments and functions are insufficient.

- [ ] Compose objects from immutable records and existing first-class functions; methods reuse ordinary `fn` parameters, closures and result contracts
- [ ] Define message application as explicit receiver/name/arguments lookup and function application, with one documented rule for binding the receiver (`self`)
- [ ] Pure state transitions return a new value (and any result); the original receiver stays unchanged, and identical inputs give identical outputs
- [ ] Assemble small capabilities with explicit record composition; disjoint members combine, shared members require agreement or an explicit resolution, and parent order never silently picks a winner
- [ ] Delegate selected messages to a contained value; forwarding and receiver binding are explicit, with no hidden superclass chain
- [ ] Adapt capabilities through explicit member selection, renaming and exclusion, reusing ordinary map/function tools wherever possible
- [ ] Support polymorphism through a shared message contract; distinguish satisfying a capability from declaring an ancestor, and reuse the type algebra for compatibility
- [ ] Keep behavior discoverable as data: inspect available messages, contracts, captured bindings and composition provenance; missing or conflicting messages produce structured errors with source locations
- [ ] Demonstrate composed behavior and captures surviving versioned execution snapshots without hidden host identity; ordinary-data YAML round trips and non-restorable inspection retain their separate contracts
- [ ] Document composition laws and boundaries: identity, associativity for compatible components, conflict behavior and observational equivalence; demonstrate them with golden cases and reproducible laws through `./manage.py`
- [ ] Demonstrate a small pure object built from independent capabilities, a delegated wrapper and an immutable update, without requiring inheritance or a parallel method evaluator

### Machine configs as type-time types

The long-range goal is a typed desired machine description, resolved without
machine contact before observation and application. The implemented operations
remain distinct: class realization fills defaults and validates a configuration;
membership checks allowed values; convergence compares configured values with
observations and preserves unmanaged extras. An empty plan is not membership in
a closed class. [The language contract](LANGUAGE.md#classes-machines-and-drift)
owns these distinctions.

Pure resolution uses the complete linked source and explicit inputs. Observations
arrive through authorized effects; planning consumes them as data, and reviewed
application performs separately authorized changes. Each supported workload must
establish freshness, idempotence and recovery with independent observations.
General machine application and fleet-wide validation remain open; the nginx
workload supplies the bounded evidence noted below.

- [ ] A machine config is a concrete type inheriting from abstract machine classes; it is fully resolved at type time with no machine contact
  - [x] A small standard vocabulary of machine classes (packages, files, services, users), declared in Helix itself (`std:machine`, `std_machine` golden case)
  - [ ] A config with an unfilled or conflicting field fails at type time with the class errors above
    - [x] The nginx configuration class reuses its input schema, realizes defaults and inherited constraints purely, and rejects incomplete/invalid/effectful declarations before requesting observation (`nginx_types`).
  - [ ] Resolution makes no contact with any machine or network, enforced by the type-time contract and not by convention
  - [ ] Facts that only a machine knows are declared as typed inputs to realization, never read during resolution
- [ ] The resolved config serializes to YAML that reads as the complete, flattened description of the machine and diffs well in `git diff`
  - [ ] Stable key order and formatting, so an unchanged config produces no diff
  - [ ] A one-field change in a class shows as a one-line change in every machine that inherits it
  - [ ] Provenance is available without drowning the description (separable or opt-in)
    - [x] `nginx.resolve` returns only complete values; optional `nginx.explain` returns versioned values and explicit/default class provenance. The repeated Docker baseline applies omitted defaults against an equivalent complete review and independently verifies convergence.
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
  - [x] Keep convergence distinct from membership: an empty plan means configured values match; extra observed fields remain unmanaged. Closed membership additionally enforces schemas, pins and constraints (`convergence_policy`, `machine_fleet`). Any future machine-satisfaction relation needs a separately defined contract
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
  - [x] Opaque `secret(ref)` inputs stay handles in resolved data and saved state; caller-granted local/SSH command stdin and SSH file writes resolve only inside the host (`secret_effects`, `ssh_host`)
  - [x] Secret resolution happens only while dispatching an explicit consuming effect, never during pure class realization; file grants remain caller inputs and are not restored
  - [ ] Error messages, plans and drift reports never contain a secret value throughout the operational stack
    - [x] Supported secret-consuming effects suppress captured streams and diagnostic contents before VM outcomes, snapshots, journals and reconciliation persist them. Ordinary plaintext inputs and later secret-bearing observations remain outside this boundary.

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
- [ ] Use friction from actual programs to choose a concrete improvement or record a deliberate deferral; do not turn every awkward expression into a feature obligation

### Pure-core test coverage and friction

Golden coverage is evidence of executable examples, not evidence of real-world adoption; the usage milestones above remain open.

- `dogfood_classics`: FizzBuzz through 20, primes below 30, and word frequencies over inline text, including empty inputs, prime boundaries, repeated spaces and Unicode words. Friction: no string split builtin; tokenization folds over character slices and explicitly handles delimiters. Trial division is suitable only for small inputs. These are executable examples; the real-use milestone remains open.

- `dogfood_config_diff`: added/removed/changed keys, nested value changes, null versus absence, unchanged and empty snapshots. Explicit references now allow direct report maps; nested changes are still reported at the top-level key.

- `dogfood_project_inventory`: missing required fields, duplicate names and dangling dependencies, plus valid and empty inventories. A declared record type now supplies required fields and collected nested violations; malformed rows retain their diagnostics without aborting duplicate and reference reporting.

- `dogfood_dependency_order`: stable ordering of diamond dependencies, missing references, cycles, self-dependencies and empty graphs; independent work completes before blocked tasks are reported. The implementation rebuilds the pending map per batch; `std:map.remove` now supplies single-key removal, but repeated immutable removals would still copy maps. Blocked results preserve the graph rather than claiming every blocked node belongs to a cycle.

- `dogfood_expense_reconciliation`: aggregate duplicate accounts and refunds in integer cents; report mismatched totals and accounts present on only one side, including zero balances; balanced and empty ledgers yield no findings. Friction: currency units and matching keys are conventions in user code, with no decimal-money type.

- `dogfood_work_queue`: stable priority sorting and dispatch through `std:list.sort_by`, with negative priorities, equal-priority payloads, sorted/reversed/empty/singleton inputs, independent calls, idempotence and source-order preservation. The original 100+ line example now shares its sort implementation with the standard library. Friction: insertion sort repeatedly copies lists and is suitable only for small queues.

### Errors as a first-class feature (section 4) — executable coverage

`dogfood_errors` covers independent malformed-row validation, a calculator with per-input recovery, kind-based retry histories, domain wrapping with retained causes, assertion-led caller repair and cleanup on success/failure. Reports retain input positions and full errors; later valid rows still finish.

The paired `dogfood_errors_regression` / `dogfood_errors_fixed` cases preserve a minimized failure and its one-input correction (`count: 0` to `count: 1`). Their saved-state diff explains the assertion failure and corrected result, using the existing golden regression workflow.

Friction: lazy bindings require assertions in the result dependency chain; wrapping retains the original error explicitly under `cause`. Pure cleanup uses a raised error to witness execution, with a separate successful-cleanup case preserving the original failure. These examples do not establish external retry or resource-management guarantees. Parser-document coordinates appear in messages; structured spans identify the parsing actor, so reports retain source and index.

### With suspend/resume (section 2)
- [ ] A long computation stopped midway, its state read in `git diff`, resumed to the same result as an uninterrupted run
- [x] Fix a mistake by hand-editing a suspended live parameter and resuming it (`snapshots`); this does not invalidate completed memoized work
- [ ] Resume a run that stopped on an error after fixing its cause by hand, without redoing the work that preceded it
- [x] Stop and resume inside nested function calls and iteration; inspect the saved locals, accumulated outputs and remaining work without consulting a trace (`snapshots`)
- [x] Move a suspended run to another directory and finish it using only its state after deleting its original source and imported file (`snapshots`); the documented runtime version remains required
- [x] Keep original and edited branches of one suspended run, finish both, and use their live-parameter state diff to explain why the results differ (`snapshots`)

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

Use Helix for its own policy when production callers demonstrate clearer behavior or better development feedback. Implementation language is not a success criterion. Section 12 retains runtime parsing, evaluation, shared type checking and independent host supervision where their contracts justify them; preserve an independent build bootstrap.

### Already within reach of the pure core

- [x] Standard-library arithmetic and list helpers are Helix source: `std/math.yaml` and `std/list.yaml` already implement min/max/clamp, sum/product/reverse/range, exercised by the standard-library golden cases. Extend these modules rather than adding equivalent Rust builtins.
- [x] Collection and text algorithms: sorting, grouping, deduplication, map merge/removal, splitting and joining as imported Helix functions over the existing collection primitives. Reuse the algorithms in the dogfooding cases; document copying costs and iteration limits before treating them as general-purpose library functions.
  - [x] Stable grouping by a string-valued callback in `std:group.by`, covered by `std_group`; preserves item order within each group, with canonical string-key order for groups and documented copying costs and iteration limits. Real tooling caller: `./manage.py inventory` invokes `tooling/inventory.yaml` to group repository files by directory and saves replayable input/state; covered by `dogfood_tooling_inventory` and CLI contracts.
  - [x] Stable comparator sort (`sort_by`) extracted from the work-queue example, plus first-occurrence deduplication (`unique`), in `std:list`; covered by `std_list_algorithms` and `dogfood_work_queue`. Both use quadratic scans/copying and runtime iteration limits; intended for small inputs.
  - [x] `std:list` access/slicing, enumerate/zip, one-level flatten/flat-map, short-circuit searches, quantifiers, count/partition, grouping/frequencies and default sorting; contracts and boundary cases live in `std_list_helpers`.
  - [x] Shallow right-biased merge and single-key removal in `std:map`, covered by `std_map`: stable key order, missing/null distinction, nested replacement, empty and Unicode keys, and checked arguments. Immutable copies can cost quadratic time/allocations; runtime iteration limits apply.
  - [x] Literal splitting and joining in `std:text`, covered by `std_text`: Unicode, empty fields, separator policy and argument contracts; copying costs and iteration limits are documented in `LIBRARY.md`.
- [x] Configuration diff, dependency ordering and validation reports as reusable Helix modules, extracted from the existing dogfooding cases. Their policies and error recovery belong in Helix; primitive lookup, equality and structured error attribution remain runtime services.
  - [x] Shallow configuration comparison in `std:diff.compare`, extracted into the authoritative module used by `dogfood_config_diff`; `std_diff` covers ordering, missing/null distinctions, nested values and invalid inputs.
  - [x] Indexed batch validation in `std:validation.batch` reuses `std:test` report policy and retains inputs and original errors; `std_validation` covers continuation and boundary contracts, and `dogfood_errors` uses it for YAML row validation.
  - [x] Stable dependency planning in `std:dependency.plan`, used by `dogfood_dependency_order`; `std_dependency` covers blocked graphs, missing prerequisites and argument contracts.
- [x] Type descriptor construction and transformation: `std:type_descriptor` owns assembly for `list_of`, `map_of`, `optional`, `default`, `union`, `enum`, `literal`, `record`, `with_field` and `secret`; the corresponding Rust assembly is removed. Public calls retain shared runtime type/field validation, default checking and argument blame, and execute the policy through ordinary evaluator calls. `type_descriptor_policy` is the golden contract; `type_algebra` checks independent constructor results, invalid arguments and records through 256 fields. Class/refinement internals and the authoritative checker remain Rust. Immutable record updates can incur quadratic copying costs.
- [x] Drift and convergence planning: `std:convergence` reuses `std:diff` for comparison and ordered plans over desired and observed maps; the replaced Rust comparison/plan implementation is removed. Public `drift`/`converge` retain class/input validation and call the policy through the existing engine. `std_convergence` covers missing versus null, nested changes, stable steps, extra-field preservation, empty plans and pure phases; `type_algebra` supplies an independent 128-field oracle and `execution` checks retained continuations. Observation and application stay separate effects.

### Next: types and callable values make the larger pieces practical

Types and callable values are implemented. The earlier blanket proposals to move
the type checker and class realization into Helix are deferred by section 12's
boundary audit: both need shared validation, predicate scheduling, resource bounds
and original source blame. Moving only assembly adds cross-language sequencing
without removing that responsibility. Reconsider only against a measured failing
caller such as `std:types.redeclare` or class-driven nginx configuration.

Machine vocabulary and lifecycle policy already use ordinary modules. Remaining
machine/fleet work belongs to sections 8 and 11, with the same reconciliation and
target-coordination obligations regardless of implementation language.

### With a few explicit runtime services

- [ ] Evaluate a workload-driven migration of formatting or inspection policy, only if it improves the existing implementation. Replacing `src/emit.rs` needs lossless scalar/source reflection, scalar-to-text and escaping support, plus a bootstrap output path that does not recursively invoke the formatter. Preserve explicit literal boundaries, call/reference forms, source spans and the existing round-trip fixpoint.
- [ ] Evaluate Helix graph passes for a concrete static-analysis workload; retain the current Rust checker until a replacement demonstrates a benefit. Requires access to the unevaluated graph, parent links, paths and spans without triggering effects; reuse the authoritative checker. Runtime cycle guards and resource limits remain enforced by the bootstrap.
  - Existing development feedback: the `static` suite checks production `std/` and `tooling/` sources and retains unresolved obligations. This exposed and repaired the checker confusing construction of an effectful provider callback with its invocation, and omitting annotated bodies in computed call heads. Production test-policy input/result annotations exercise runtime contracts; mutation checks require early structural field diagnostics and source-local errors for absent policy references/callbacks. Missing-name analysis reuses runtime lexical declarations without loading imports or guessing dynamic members; unsupported higher-order obligations remain explicit. The source graph traversal is shared; the checker remains Rust, so this does not complete the graph-pass migration.
- [ ] Improve test and task feedback on actual production workloads. Existing pure policy is implemented below; moving filesystem discovery, process supervision or build/lint invocation requires a demonstrated benefit. Keep `./manage.py` as the root entrypoint, independent bootstrap and host-level detection of a broken interpreter.
  - [x] Pure test policy in `std:test`: assertions, expected errors, per-case recovery, table/state fixtures, bounded properties, differential checks and serializable reports. `dogfood_testing` composes selection, failure reruns, fixture factories, shrinking and nested suites; `std_test_failure` proves a failed report becomes a structured VM error. Filesystem/process orchestration remains host-owned.
  - [x] Robustness mutation policy in `std:fuzz.plan`: Helix selects ordered source-slice plans for line prefixes, line deletions and character prefixes. The production Python harness materializes one variant at a time and independently supervises interpreter failures. `std_fuzz` and harness parity contracts cover Unicode boundaries, duplicate ordering, the largest existing fixture and malformed generator results. Robustness executes stdin mutations with the fixture's original import base, and retained replay recipes restore that context; independent sibling/transitive-import controls check actual original and mutated results rather than accepting an incidental missing-import error. Host orchestration remains independently supervised.
  - [x] Golden-test decision policy in `std:test.golden_plan`: production tests use Helix to decide deterministic-output failures, exact expected-text comparison and ordinary-root replay outcomes. Python captures observations, enforces the policy response contract and independently rejects interpreter failures. Golden fixtures and harness fault injection check the policy and its host boundary. Discovery, process execution and build/lint orchestration remain host responsibilities.
  - [x] Production suite orchestration in `tooling/test_policy.yaml`: `./manage.py test` uses Helix for suite selection/order, standard-library prerequisites, blessing skips, result statuses and report aggregation. Python executes the plan, preserves captured observations and independently rejects missing/unauthorized tasks or false-success responses. `dogfood_test_orchestration` and `harness` cover independent expected plans, policy crashes, malformed responses, retained failures and realistic report sizes. Saved versioned reports also drive failed-suite reruns through the same policy and planner: this removes manual extraction of failed suites after mixed outcomes. Host validation rejects inconsistent reports and altered selections before tasks run; a prior report never grants blessing or Docker authority. Empty retry selections remain empty, and failed standard-library prerequisites retain their runnable fixture companions. Explicit fixture selections persist as independently validated per-suite paths through subsequent reports and reruns; missing paths fail before building, while legacy and unfiltered reports retain whole-suite behavior. Standalone startup checks remain build-free. Filesystem discovery, process supervision/concurrency and build/lint invocation remain host-owned; future changes need a measured feedback target.
  - Measured feedback uses the existing speed suite and independent output
    oracles: production report assembly (including retained failure logs),
    `std:types` reconstruction of fleets and 128-field declarations, and
    `std:fuzz.plan` over fixed Unicode/CRLF and real fixture inputs. Removing
    redundant report flattening, record reconstruction and mutation bookkeeping
    improved local debug medians; immutable copying costs remain. These are
    workload observations, not portable performance guarantees.
  - Text results flush at suite boundaries; final policy aggregation retains an
    independent failure path and JSON remains a complete report. Deterministic
    harness checkpoints verify early visibility without timing assumptions.
  - Remaining friction: output-map fields shadow same-named enclosing bindings;
    lazy bindings can repeat work in callbacks. Existing distinct names, `dict`
    and eager function arguments address these cases without new evaluation rules.

### Migration bar

Move one complete responsibility at a time: a Helix module becomes the authoritative implementation, production callers use it, and the replaced Rust policy is removed. A forwarding wrapper around the same Rust implementation does not count. Preserve public syntax, structured errors, locations and state round trips; prove parity through `./manage.py test` golden cases, malformed-input coverage where applicable, and realistic size checks. A temporary comparison against the old implementation can establish parity, but must not leave two policy engines to maintain. Defer a self-interpreter or YAML parser rewrite until lossless graph reflection, explicit continuations and bootstrap recovery make it a concrete improvement.

---

## 11. Replacing Ansible

Replacement means operating real machines reliably within a declared support scope, not merely invoking shell commands from YAML. Start with Linux hosts reached locally or over SSH; expand the supported platforms and resources from demonstrated needs. Playbook syntax compatibility and parity with every Ansible collection are not initial requirements. A scoped replacement must state what it cannot manage.

Reuse the type/class algebra, `std:machine`, `drift`, `converge`, `std:dependency` and the existing effect engine. Section 8 owns configuration resolution, pure planning and realization semantics; sections 2–3 own snapshots and host effects. This section owns the operational capabilities and acceptance evidence needed to turn those foundations into an Ansible replacement. Existing data comparison and in-memory effect records are foundations, not completed machine management.

### First useful replacement: one Linux host

The Docker nginx acceptance specification below is complete, with separate SSH and durable-recovery qualification. Its pinned container scope is narrower than this parent milestone: a real playbook migration and general Linux service management remain open.

- [ ] Replace one real configuration playbook with an end-to-end Helix configuration
  - [ ] Declare a package, a managed configuration file and a service using the existing machine vocabulary
  - [ ] Observe their actual state through authorized effects and produce a plan using section 8's realization contract
  - [ ] Review and explicitly apply that plan, then verify the resulting state independently
  - [ ] A second run performs no mutations; deliberately introduced drift produces only the necessary repair
  - [x] Demonstrate both local execution and SSH execution on a disposable host (`ssh_nginx`; the same policy, independent observations and durable controller-death recovery)
  - [x] Record the supported distribution, resource semantics and remaining limitations alongside the example ([operator contract](tests/fixtures/docker_nginx/README.md); one pinned Debian container/root/helper combination)
- [x] Extend the same lifecycle beyond one workload to general typed Debian host resources: groups, users, exact-version packages, directories, files and symlinks (`std:linux`, `docker_linux`)
  - [x] Derive prerequisites (user→group, owners, declared ancestors, explicit `after`) into a validated dependency graph that is part of the review; reject unknown references, cycles and present-requires-absent before mutation
  - [x] Explicit `state: absent` removes in reverse order; omission never deletes; unmanaged siblings survive; a failed resource skips exactly its transitive dependents
  - [x] Independent probes of actual container state judge baseline, updates, drift repair, removal, stale reviews, failure, false success, authority and lifecycle
  - [x] Two container-resident Helix controllers configure each other over pinned SSH, with independently verified no-op convergence and targeted drift repair (`docker_linux` peers); Docker supplies transport/trust bootstrap, and concurrent-controller/fleet recovery remains unqualified
  - [ ] Services, boot persistence, templates, cron, firewall and other kinds remain unqualified ([operator contract](tests/fixtures/docker_linux/README.md))

### Docker nginx acceptance specification — completed local milestone

**Outcome:** one readable Helix configuration installs nginx, manages its configuration and static content, and keeps it serving the declared response inside a disposable Linux container. An operator can preview, review, apply, verify, repeat without mutations and repair drift. Failures expose actual partial progress. The implementation and qualification evidence below cover this local container scope; the broader playbook-replacement obligations above remain open.

#### Scope and execution boundary

- [x] Qualify one Debian-based Linux image and architecture, pinned by digest, with a reproducible package source/version policy. Record resolved image, package and controller versions in run artifacts. Keep nginx absent in the clean baseline; do not bake the desired state into the fixture. Retain bootstrap dependencies separately from resources Helix manages.
- [x] Run the Helix controller inside the target container for this first local-execution milestone. The Python harness owns Docker provisioning and independent observations from outside the Helix process. Docker exec is harness transport, not a new Helix effect or a substitute for an SSH adapter.
- [x] Use the existing local command host and explicit caller grants. Docker access stays with the harness; the managed container receives no Docker socket, privileged mode, host PID namespace or writable host-system mounts. Scope writable project artifacts to a dedicated run directory, bind any published HTTP port only to loopback and allocate ports without fixed global names.
- [x] Define an explicit nginx process lifecycle suitable for this container, with child reaping, bounded startup/shutdown and actual readiness checks. Document the supported supervisor/process arrangement. Do not claim systemd enablement, boot persistence or general service-manager support from this test.
- [x] Restrict the workload to one host, one package family and one nginx instance. Fleet scheduling, TLS/ACME, additional distributions and general package upgrades remain separate milestones. SSH and disk continuation qualification are recorded separately below. Container recreation supplies test isolation; it is not application rollback.

#### User configuration and architecture

- [x] Provide one primary example under `tests/programs/` with typed inputs for package version policy, listen port, server identity and response content. The user declares package, file and service intent and their dependencies; provider callbacks, shell lifecycle plumbing and fault-injection hooks belong in reusable implementation or fixtures. Keep a second input variant to demonstrate reuse without copying the resource lifecycle.
- [x] Reuse `std:provider`, `std:orchestration`, `std:dependency`, the existing type/configuration vocabulary and effect host. Inspect existing `std:command` and provider examples before introducing helpers. Keep planning pure over explicit observations; neither the Python harness nor a second interpreter may make the desired-state decisions on Helix's behalf.
- [x] Define ownership for the nginx package, managed configuration and content files, and managed process. Preserve an unrelated sentinel file and unrelated process throughout the suite. Missing declarations do not authorize deletion; any removal scenario must use explicit ownership and removal intent.
- [x] Specify file content, ownership and mode, deterministic rendering, candidate validation with nginx, and atomic replacement of each live file. Validate a candidate configuration before activating it. If a multi-file update can fail between replacements, expose that partial state and the recovery path; do not describe per-file atomicity as a transaction.
- [x] Start nginx only after required package and files succeed. For an already running service, coalesce successful configuration changes into one reload after validation. A content-only change that nginx serves directly requires no reload. A no-op requires neither restart nor reload; metadata repair follows a documented policy. Readiness must verify the declared response, not merely an existing PID.

#### Review and application contract

- [x] Expose resolve, observe, plan, apply and verify through discoverable existing tooling, adding only the smallest missing interface. Pure resolution errors occur before mutation. Observation may query the target but must not install packages, refresh package metadata, write managed files or change service state.
- [x] Plans identify target and resource identities, desired inputs, relevant observed state, proposed changes, dependencies and unsupported/unknown observations. File diffs and package/service actions must be understandable without inspecting evaluator internals. Unknown state is a failure to plan safely, never evidence of absence.
- [x] Apply only an explicitly reviewed plan with caller authority. Re-observe relevant state and reject a stale review before its affected mutation. A changed desired input, target identity, provider revision or relevant observation invalidates the review. Document the remaining check-to-mutation race; use one controller in this scope and do not imply concurrent-controller locking.
- [x] Verify each applied resource with fresh observations and verify the final HTTP response independently. Preserve unchanged, changed, failed and skipped distinctions, source locations, dependency causes and completed effects. A successful command exit with an unmet postcondition is failure.
- [x] Specify clean-run, drift and failure outcomes for automation, using existing report and exit-status conventions where applicable. Reports must make an interrupted or partially applied run distinguishable from a successful one. Saved plans and reports are ordinary data, not restored execution or authority grants.

#### Acceptance scenarios

Each scenario must assert actual package/file/process/HTTP state and relevant action counts independently of Helix's summary. Reset to a known fixture when scenarios require independence; preserve the same container for convergence and recovery sequences.

| Scenario | Required evidence |
| --- | --- |
| Clean baseline and preview | nginx is absent; resolution and repeated previews leave package, managed paths and processes unchanged; equivalent observations yield equivalent plans. |
| First application | nginx is installed at the selected supported version; declared bytes, owner and mode are present; the service starts and serves the exact expected response. |
| No-op convergence | At least two subsequent observe/plan/apply cycles perform zero mutating actions, including package refresh, file replacement, start, stop and reload. Confirm stable file identity/metadata where applicable and stable nginx master identity. |
| Reviewed configuration update | Change two managed configuration fragments in one application; the valid update becomes active with exactly one reload and no unnecessary package action or restart. |
| Content update | Change the served body; the new body is observed without a service reload or restart. |
| Independent drift | Separately alter configuration bytes, file mode/owner, remove a managed file, stop nginx and remove the package. Each fresh plan identifies the affected resources, repairs only necessary state and converges again. Account for actual package removal side effects. |
| Combined drift | Combine missing package/files and stopped service; dependency ordering repairs the complete state without duplicate service actions or mutation of unmanaged sentinels. |
| Invalid desired configuration | Candidate nginx validation fails before replacing the corresponding working live configuration or reloading it; the previous response remains available, with source-linked failure and no false success. |
| Invalid input/dependencies | Wrong input types, unsupported settings, missing prerequisites and dependency cycles fail before resource mutation. |
| Stale review | Change a managed observation or desired input after review; application rejects the obsolete action. A fresh review permits the repair. Repeat against a recreated target with a different identity. |
| Missing authority/unknown state | Deny a required grant or make observation fail; report the resource and cause, preserve existing state and skip dependent mutations. |
| Package failure | Inject unavailable package/version, inaccessible repository and package-lock contention; use bounded waits, report failure and skip resources whose prerequisites are unmet. Distinguish infrastructure setup failures from expected injected failures. |
| File failure | Inject permission denial and a failed replacement; report any completed writes accurately, preserve unaffected files and avoid an unsafe reload. |
| Service failure | Occupy the listen port or force start/reload failure; readiness fails within a bound, the report preserves partial progress and unaffected resources remain intact. |
| False-success command | A fixture command returns success without accomplishing its change; postcondition observation rejects it. |
| Timeout and cancellation | Interrupt a long-running operation and a run between resource actions; bounded cleanup and dependent-action suppression follow the existing command-host contract. No success or automatic rollback is claimed. |
| Controller death and fresh reconciliation | Kill the controller after a confirmed mutation but before its acknowledgement, and separately after configuration installation but before reload. A fresh invocation observes actual state, requires a fresh review and converges without trusting the old report or blindly replaying actions. Define how unapplied configuration activation is detected or conservatively repaired. |
| Explicit removal | Stop the managed service and remove explicitly owned resources according to declared policy; preserve unmanaged content and processes. A second removal is a no-op. |
| Container lifecycle | Restart the container and reconcile under the declared process model; recreate a clean container and repeat the first-application sequence. Neither case counts as restoring a suspended Helix machine. |

#### Repeatable harness and evidence

- [x] Integrate a named `docker_nginx` suite into the existing test policy and runner, discoverable with `./manage.py test --describe` and runnable with `./manage.py test --suite docker_nginx`. All provisioning, scenario selection, assertions and cleanup are routinized Python tooling reached through `manage.py`; no ad-hoc shell test procedure is the acceptance authority.
- [x] Distinguish prerequisites and fixture build/pull failures from interpreter/provider failures. An explicitly requested suite with unavailable Docker or missing required capabilities fails with actionable diagnostics; any default-run opt-out is an explicit reported skip, never passing acceptance evidence. Keep ordinary bootstrap/help and unrelated suites independent of Docker.
- [x] Give each run unique labeled containers, networks, ports and project-local `runtime/` artifacts. Bound command, scenario and suite duration and captured output. Clean owned resources on success, failure and cancellation; support explicit failure retention and discoverable cleanup of retained resources. Never remove unrelated Docker resources or use `/tmp` for test artifacts.
- [x] Preserve versioned reports, plans, input variants, image/package identities, independent observations and action traces needed to explain a failure. Provide one replay command per failed scenario. Normalize volatile identities/timestamps only in golden projections; retain raw evidence. Avoid embedding credentials in images or reports.
- [x] Reuse existing golden, provider, composition and effect suites for pure contracts and diagnostics; add Docker coverage for behavior requiring the actual package manager, filesystem and nginx. Independent Python checks must inspect those systems directly, not merely compare two Helix reports. Prove sensitivity with a suppressed reload, a false no-op and a false-success mutation fixture.
- [x] Run the full required scenario set from clean provisioned state, repeat it to expose leaked state and report total duration plus bounded resource observations. Preserve evidence that the negative scenarios failed for their intended reason and that later reconciliation restored the expected response.

#### Completion and consolidation decision

- [x] Publish the runnable example, supported environment, operator workflow and complete acceptance report. Separate container-specific process behavior from future system service support. The separate SSH qualification below closes only disposable-host execution; broader replacement obligations stay open.
- [x] Review the resulting user program against its original intent: package, configuration, content and running service. Identify every new public concept needed, remove redundant mechanisms and document justified remaining complexity under section 12. Framework implementation and a successful happy-path demo alone do not complete this target.

Evidence: the [primary example](tests/programs/docker_nginx.yaml) and
[alternate input](tests/programs/docker_nginx/alternate.yaml) use
[`std:nginx`](std/nginx.yaml). The [fixture/operator contract](tests/fixtures/docker_nginx/README.md)
and [test entrypoint](tests/README.md#docker-nginx-acceptance) describe execution,
review, failure semantics and supported environment. The
[qualification record](tests/fixtures/docker_nginx/qualification.json) preserves
all 17 scenarios repeated twice, timings, resource samples, exact versions and
the complete raw report's location and digest. Existing golden, execution,
provider, composition, command-host and tooling checks supply the pure and
cross-cutting regression evidence. Section 12 records the consolidation decisions
and unresolved semantic friction. This is typed-record application through the
existing provider contract, with pure class-driven input realization; it does
not establish section 8's general machine-application workflow.

The separate [SSH qualification](tests/fixtures/docker_nginx/ssh-qualification.json)
records fresh-container runs of the same policy, including SSH-adapter integration, with an external controller
and caller-granted target alias.
`ssh_nginx` checks local/SSH convergence, repeated no-op, stale reviews, drift,
capability denial and host-key rejection. After a remote file mutation it kills
the controller, independently verifies actual files, rejects uncertain replay,
reconciles the exact request and resumes with fresh grants. HTTP and action
observations prove activation without repeating the mutation. This qualifies
one pinned disposable Debian container with the fixture helper and root SSH
account; it is not an arbitrary-host bootstrap or a real playbook migration.

### Inventory, connections and observed state

- [x] Select and describe hosts without implicit configuration precedence
  - [x] Typed inventory supports stable host identities, groups, connection settings and explicit overrides with provenance
  - [x] Host/group selection and exclusions resolve to a reviewable target list before mutation
  - [x] Dynamic inventory is an explicit host input with validation; discovery never runs inside pure configuration resolution
  - [x] Missing hosts, duplicate identities and incompatible settings produce structured diagnostics
  - Evidence: `./manage.py test --suite host_inventory` exercises the pure
    `std:inventory` module with static and supplied dynamic data, an independent
    selection oracle, ordered override histories and invalid-input diagnostics.
    `host_inventory` golden coverage fixes target ordering, override histories,
    selection, empty inputs and invalid-input diagnostics.
    Validation includes unselected hosts and empty inventories; failures return
    no targets. The [runnable example](tests/programs/host_inventory.yaml) and
    [inventory contract](HOST.md#host-inventory) document the versioned schema,
    first-error reporting and bounded in-memory support. Connection descriptions
    confer no authority; discovery, SSH adapters and resource application remain
    separate milestones below.
- [ ] Supply dependable local and SSH host adapters through the existing effect boundary
  - [x] SSH supports host-key verification, existing key/agent authentication, connection reuse and jump-host configuration
    - The `ssh_*` effects verify host keys strictly against a mandatory caller-supplied `known_hosts` (no trust on first use), authenticate non-interactively with a granted identity file or the caller's ssh-agent, ignore user/system ssh config, hold a `jump` hop to the same host-key/identity policy, and reuse authenticated connections only for identical grants within a private `perform` session under an owner-only `--ssh-control-dir`; session teardown closes them. Reuse needs a local filesystem (OpenSSH links sockets, which NFS refuses); a killed controller can leave a master for up to 30 seconds.
  - [x] Privilege escalation is explicitly authorized and distinguishes connection identity from execution identity
    - Caller-granted `become` runs a reporting shell under non-interactive sudo, preserving connection versus execution identity. The opt-in disposable Docker SSH fixture verifies successful root commands, stdin, root-owned files and actual sudo-policy refusal without requiring the controller account to emulate the remote policy.
  - [x] Remote argument transport and file transfer preserve bytes and prevent accidental shell interpretation
    - Every remote word is single-quoted; `ssh_command` carries remote `stdin`; `ssh_file_write` streams text to a staged temporary file, verifies the received byte count, applies the mode and renames into place (per-file atomic, no multi-file transaction); `ssh_file_read` returns the exact text. Tests compare arguments, stdin and files byte-for-byte, including CRLF, unicode, no trailing newline and shell metacharacters. Limits: UTF-8 text up to 1 MiB (non-UTF-8 content is an `io` error, matching Helix strings), absolute paths only, no directory or binary transfer, ownership is the executing account.
  - [ ] Connection, authentication, privilege, transport and remote-command failures remain distinguishable
    - Remote command completion (including exit 255) and sudo-wrapper completion use nonce-tagged reports instead of diagnostic text. Pre-session host-key, authentication and connection subdivisions still depend on OpenSSH diagnostics and remain heuristic; a typed SSH protocol boundary is still needed. Framing is not protection against hostile programs inspecting their parent process.
  - [x] Document and check managed-host prerequisites; bootstrap missing prerequisites through an explicit supported path
    - [`HOST.md`](HOST.md#ssh-remote-commands) lists the remote tools the file effects need; `std:ssh.check` observes the gaps under the connection identity and never mistakes a failed check for an empty one; `std:ssh.bootstrap` runs only a caller-declared installer (escalated through a granted `become` when set) for an observed gap and re-checks, so success is an observation, not an exit status. No package manager or distribution is inferred, and no distribution-specific installer is qualified yet.
  - Evidence: `./manage.py test --suite ssh_host` exercises all of the above against unprivileged loopback `sshd` servers with throwaway keys and independently counted authentications. The opt-in `HELIX_SSH_DOCKER=1` path adds a disposable container SSH target for escalation and file acceptance. `ssh_nginx` separately qualifies the nginx workload and durable recovery on a disposable Debian host. General platform support and reliable pre-session failure kinds remain open.
- [x] Gather typed facts for bounded Ubuntu 26.10/x86_64 realization through explicit local/SSH commands; `std:facts` qualifies containers and a disposable provider fixture. Normally booted systemd services and general resource observations remain separate.
  - [ ] Detect supported operating systems, package managers and service managers before selecting adapters
  - [ ] Observations carry host identity and freshness information; unknown or inaccessible state is never treated as absence
  - [x] Collection is explicitly uncached; application refreshes fact evidence and reruns pure configuration before provider mutation
  - [x] Define a typed, versioned facts schema (distribution and release, kernel, architecture, hostname/machine identity, network addresses, mounts and storage, CPU/memory, installed-package index, running services) with explicit `unknown` and `inaccessible` states distinct from absence
  - [x] Gather facts only through caller-granted read-only effects at an explicit phase; pure configuration may consume supplied facts but never trigger discovery, and fact collection cannot mutate the host
  - [x] Bound collection time, output and per-fact cost; allow selecting which facts are gathered; report partial gathering and per-fact failure causes
  - [x] Let class constraints, resource selection and inventory groups depend on supplied facts, with the consumed facts recorded in the review so a plan is stale when they change
  - [x] Support caller-defined custom facts as typed, versioned ordinary data from a granted command, validated before use
  - [x] Verify against disposable hosts that reported facts match independently observed state across each supported distribution

### Resource providers and change semantics

- [x] Establish one discoverable provider contract for observation, planning, application and verification
  - [x] Providers declare argument/result types, capabilities, supported platforms, check/diff support and failure behavior
  - [x] Distinguish unchanged, changed, failed, skipped and unsupported outcomes using structured data
  - [x] Pure plan generation shares section 8's semantics; a dry run never silently applies a resource whose preview is unsupported
  - [x] Re-observe after mutation and report unmet postconditions instead of trusting a successful command exit alone
  - [x] Define ownership and deletion explicitly so absent configuration does not accidentally remove unmanaged resources
  - Evidence: `./manage.py test --suite provider_contract --suite type_algebra --suite library`
    checks `std:provider` with typed callbacks, pure data-only plans compatible
    with `converge`, unsupported-preview gates, exact fresh review matching,
    authorized file changes, no-op reapplication, drift repair and explicit
    removal. The `provider_contract` golden covers plans, ownership/removal gates,
    unsupported previews, stale reviews, no-ops and unmet postconditions.
    Independent host-state assertions cover preservation of unmanaged
    files, mutation followed by failure and unmet postconditions. See the
    [runnable example](tests/programs/provider_contract.yaml) and
    [provider contract](HOST.md#resource-provider-contract). Callbacks are
    trusted provider implementations, capabilities remain caller grants, and
    revision identity is author-declared. Pure phases admit loader-authorized linked
    file modules. Caller-owned journals supply durable dispatch and shared-domain target locks
    coordinate cooperating controllers. Remote fencing and general production
    resource providers remain separate milestones.
- [ ] Implement a useful initial resource library behind that contract
  - [ ] Packages and repositories: presence, absence, versions, metadata refresh and package-manager locking
  - [ ] Files, directories and links: content, ownership, permissions, atomic replacement and explicit removal
  - [ ] Configuration rendering: deterministic templates, validation before installation and reviewable content diffs
  - [ ] Services: enabled/running state, reload/restart policy and readiness checks
  - [ ] Users and groups: membership, ownership implications and authorized keys with explicit removal semantics
  - [ ] Artifacts: authenticated retrieval where needed, pinned versions and integrity verification before use
  - [x] Arbitrary commands: explicit guards and change/failure predicates; no implied idempotence or reversibility
    - Evidence: `./manage.py test --suite command_provider` exercises 31 scenarios
      for `std:command` through the existing provider lifecycle and bundled local
      command host. Independent process logs and file assertions cover previews,
      guarded no-ops, drift repair, stale reviews, argument boundaries, authority
      denial, explicit failure/change classification and unmet postconditions.
      The `command_provider` golden fixes preview/no-op reports, explicit result
      classification and rejection of non-boolean or effectful guards.
      Predicates are pure and boolean-checked; verification requires a fresh
      satisfied guard. See the [example](tests/programs/command_provider.yaml)
      and [contract](HOST.md#guarded-command-resources). Preview describes
      proposed execution, not predicted content changes. This module’s remote use,
      secret redaction, target locking and rollback remain unqualified; durable
      dispatch uses the separate caller-owned journal and explicit reconciliation.
- [x] Compose resources and reusable configurations without a second execution model
  - [x] Dependencies reuse `std:dependency` where suitable and reject missing prerequisites and cycles before mutation
  - [x] Change notifications coalesce service reloads/restarts and run only after their prerequisites succeed
  - [x] Reusable configuration modules expose typed inputs, outputs and versioned dependencies using existing imports and functions
  - Evidence: `./manage.py test --suite resource_composition --suite library`
    checks `std:orchestration` against an independent dependency-order oracle,
    observable callback/file effects, failure gates, coalesced handlers and retained
    VM continuations. `resource_composition` and `resource_composition_module`
    goldens cover dependency failures, coalesced handlers, failure gates and the
    pinned typed module. Full graph validation precedes executor callbacks; handlers
    run once after resources and require successful prerequisites and notifiers.
    The [typed module example](tests/programs/resource_composition.yaml) includes
    relocatable, vendored dependencies with explicit provenance. See the
    [composition contract](HOST.md#resource-composition) for sequential ordering,
    resource-only handler prerequisites and partial-failure reports. Versioned
    snapshots and journals now retain these ordinary continuations; general service
    providers and rollback remain separate milestones.

### Fleet orchestration and failure recovery

- [ ] Define and qualify fleet recovery, healing and regeneration as an operational goal. Before implementation, clarify what each means, what failures and environments it covers, what state and authority must survive, and what independent evidence establishes success. In particular, leave regeneration's scope open until explicitly defined; do not infer whether it means rebuilding controller state, reconverging surviving hosts, replacing destroyed hosts, or another lifecycle. The recovery requirements below are a starting point, not a complete definition of this broader goal.
- [ ] Run across a fleet with bounded, observable scheduling
  - [ ] Support concurrency limits, serial batches, canaries and explicit failure thresholds
  - [ ] Express cross-host dependencies and controller/delegated tasks with unambiguous execution targets and authority
  - [ ] Gate rolling changes on health checks and stop subsequent batches when the declared policy fails
  - [ ] Support reboot, disconnect and reconnect workflows with bounded readiness waits
- [x] Make long-running operations practical — bundled local synchronous command adapter
  - [x] Replace the bundled host's fixed 10-second command deadline with caller-controlled bounded timeouts
  - [x] Support bounded output capture and progress reporting for package installs and deployments
  - [x] Define cancellation and descendant-process cleanup so a timed-out controller does not silently leave mutations running
  - [x] Retry only explicitly classified transient failures with bounded attempts and delays
  - Evidence: `./manage.py test --suite long_commands --suite effects --suite library`
    exercises real subprocesses, extended deadlines, cumulative capture budgets,
    metadata progress, classified retries, cancellation through both the interpreter
    and project wrapper, descendant liveness, and callback failure/panic cleanup.
    The `long_commands` golden fixes the pending command request; the suite
    performs that same source past the old deadline and checks capture/progress.
    The caller owns all policy; time and output budgets span attempts and delays.
    Unix process groups provide local cleanup, not rollback or containment of
    deliberately detached descendants. SIGKILL/controller crashes leave uncertain
    requests for journal-backed reconciliation; process cleanup and independently
    observing remote mutations remain caller/provider obligations.
    See [bounded command operations](HOST.md#bounded-command-operations) for
    supported semantics and limits; this does not complete resource providers or
    qualify a real Ansible workload.
- [ ] Recover safely from partial application, building on section 2's durable snapshots
  - [x] Persist plan identity, target identity, provider versions and step outcomes for the scoped nginx lifecycle; restored execution obtains fresh caller grants (`ssh_nginx`, versioned snapshots)
  - [x] Caller-owned journals distinguish undispatched pending requests, completed success/failure and uncertain dispatch after controller death (`durable_effects`, `ssh_nginx`); this is one journal, not fleet-wide coordination
  - [x] Refuse uncertain replay until explicit exact-request reconciliation records an independently observed outcome (`durable_effects`, `ssh_nginx`); branch-local effect IDs do not establish exactly-once external execution, and the journal cannot prove the caller’s observation
  - [ ] Detect stale plans and conflicting concurrent controllers through preconditions and suitable locking
    - [x] A caller-selected target lock excludes cooperating controllers using different journals in one shared lock domain; persistent held markers block reuse after controller death until explicit quiescence acknowledgement (`target_coordination`). Remote fencing, partitions and nonparticipating writers remain open.
  - [ ] Expose partial success and pending work; recovery never reports an entire fleet successful because some hosts completed
    - [x] Read-only `checkpoint` reports completed outcomes, source paths, exact pending work and the required recovery action for one journal, including while its writer is active (`durable_effects`).
  - [ ] Declare compensations and irreversible operations explicitly; external rollback is not implied by immutable VM state
  - [ ] Fleet recovery: resume an interrupted multi-host run from one fleet-level record without trusting a whole-fleet success flag
    - [ ] Persist a fleet run identity, the reviewed per-host plans, batch/canary position and each host's journal location; restoring supplies fresh grants and never replays uncertain effects
    - [ ] Classify every host as unstarted, applied, failed, skipped by policy or uncertain, and show the exact next action per host and for the run
    - [ ] Reconcile uncertain hosts independently, then continue only the remaining batches whose failure policy still holds; completed hosts are re-verified by fresh observation, not assumed
    - [ ] Preserve the reviewed plan across restart and detect hosts whose observed state or identity changed since review
    - [ ] Tolerate controller death between batches, a host unreachable on resume and a second controller starting the same run (lock or refuse); a lost fleet record degrades to per-host journals, never to silent re-application
    - [ ] Report fleet status as concise text and versioned data with documented exit statuses for resumable, blocked and complete runs
    - [ ] Qualify on a disposable multi-host fixture with controller kills at batch boundaries and mid-host; no exactly-once claim beyond what independent observation proves

### Credentials, operator workflows and extensibility

- [ ] Complete section 8's secret-reference contract throughout the operational stack
  - [ ] Resolve credentials only at authorized host boundaries and support external secret providers
    - [x] Private caller-granted files supply opaque local/SSH command inputs and SSH file contents; external secret-manager protocols remain open.
    - [ ] Secret-manager integration: resolve named secret references through caller-granted adapters (for example Vault/OpenBao, cloud secret stores, `pass`/`sops`-style files or a granted command), never through source, inventory or snapshots
      - [ ] A secret reference is typed ordinary data naming provider, path/key and optional version; resolution happens only in the host at the apply boundary, and the resolved value stays opaque
      - [ ] Adapters authenticate with caller-granted identity, support bounded timeouts and distinguish missing secret, denied access, unreachable manager and malformed response
      - [ ] Plans and reviews bind the reference and, where the manager exposes one, a non-secret version/digest, so a rotated secret makes a review stale without disclosing the value
      - [ ] Resolved values never reach command arguments, logs, diffs, captured output, errors, journals or snapshots; verify this with canary secrets in every output channel
      - [ ] Define caching (default none), lease/TTL expiry and explicit rotation behavior; dry-run previews resolve nothing and show only references
      - [ ] Qualify one real manager (a disposable Vault/OpenBao container) behind the same adapter contract; additional managers are separate work
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

## 12. Complexity backpressure and consolidation

Helix earns its complexity through readable programs, understandable execution and completed real workloads. Small syntax, fewer Rust lines, more Helix code and more checked boxes do not establish simplicity. Count the concepts a user must learn, the interactions an implementer must preserve and the obligations an operator must meet. Library APIs and data protocols also carry these costs.

### Admission and stopping rules

These rules apply continuously, including to features already listed above. Record the reasoning in the change description and existing reference documentation; do not create a parallel approval system or feature ledger.

- Every new public concept, builtin, evaluation rule or protocol must name a concrete failing workload, show the closest solution using existing facilities, and explain why that solution is insufficient. Hypothetical extensibility and parity with another language are insufficient reasons.
- Prefer ordinary functions and explicit composition. A new runtime mechanism must identify the semantic or measured resource requirement that existing modules and functions cannot meet. Moving an implementation into Helix does not by itself simplify its public contract.
- Explain the added obligations: user-visible rules, affected interactions, diagnostics, resource costs and compatibility. State what existing mechanism is removed or consolidated, or why a net increase is necessary for the workload. Do not force unrelated removals to satisfy a feature-count quota.
- Evaluation changes must account for their affected interactions with lexical scope, laziness, memoization, error handling, effects, stepping and retained machines. Use focused regressions through `./manage.py` to demonstrate observable behavior; avoid a second evaluator or an exhaustive combinatorial test project.
- When supporting machinery expands beyond the chosen workload, narrow or defer the feature. Correctness, security and diagnostic repairs remain necessary; a repair that introduces new semantics still needs the same justification.
- Simplification must preserve required behavior or explicitly migrate it. Do not hide complexity by dropping independent failure detection, weakening tests, suppressing diagnostics or omitting limitations.

### Finish one defining workload before broadening

- [x] Select one active consolidation target: section 11's Docker nginx acceptance specification. Its smallest useful sequence is clean preview, reviewed installation/configuration/start, independently verified HTTP response, no-op reapplication and drift repair. The full specification adds failure and fresh-reconciliation evidence before completion. Implementation and local Docker acceptance are complete; the qualification record above bounds that claim. Fresh-process continuation restoration now has separate section 2 acceptance evidence; effectful reversibility beyond section 2's bounded promise remains open.
- [x] Complete that target using the existing architecture wherever possible, with repeatable acceptance evidence through `./manage.py`, an example a user can follow and explicit remaining limitations. Supporting frameworks alone do not close this item.
- [x] Until that evidence exists, defer unrelated language expansion, provider/platform breadth and dogfooding migrations pursued only to increase the amount of Helix code. Keep the independent bootstrap and host failure detection. Replacing the active target requires recording why it failed to provide useful evidence and what work is being deferred.
- [x] After completion, review which mechanisms the workload used, which complicated it and which remain speculative. Remove, consolidate or explicitly defer unnecessary surface before selecting the next expansion. Completion does not automatically authorize the rest of the roadmap.

### Consolidate the existing semantic surface

Extending to general Linux resources (`std:linux`) moved the nginx-era local/SSH
helper framing into `std:helper`; `std:linux` uses it. `std:nginx` still carries
its own copy because its goldens and qualification digests are pinned, so
migrating it is a deliberate consolidation, not part of that extension.

The nginx workload establishes these decisions; the reference manuals and
acceptance suites retain implementation detail:

- Keep desired-state policy in `std:nginx`, provider lifecycle in `std:provider`
  and dependency scheduling in `std:orchestration`. Fixture helpers supply host
  mechanisms and independent observations. Pure class realization reuses the
  input schema, defaults and constraints; `nginx_types` and Docker acceptance
  exercise the result.
- Reuse ordinary computed-map member lookup to expose a small module API.
  `computed_members` and execution regressions cover the resulting continuation,
  captures, blame, effects and retained machines; no new syntax or actor is needed.
- Require fresh observations, nginx worker retirement and exact HTTP responses
  before claiming activation. Atomic file replacement remains partial progress,
  not rollback. SSH reuses the same lifecycle; its qualification does not imply
  general service management or platform support.
- Evaluate linked file and embedded modules through the same pure engine.
  Loader confinement and effect rejection remain authoritative. Reproducibility
  requires the complete linked source, and preflight remains conservative
  scheduling over that engine. The [phase contract](LANGUAGE.md#type-time-evaluation)
  owns timing, input identity and handler/function/import boundaries; `type_time`,
  `execution` and provider suites preserve them.
- Retain the remaining friction as evidence for future work: lexical fields can
  shadow outer names, YAML call wrappers are verbose, and review/phase/effect
  metadata needs careful reading. These costs do not themselves justify new
  syntax, another evaluation mode or broader providers.

The audit must produce decisions and executable examples, not just an inventory. Retaining a mechanism is a valid result when its distinct purpose and cost are demonstrated. Any behavior change needs a documented migration and regression coverage.

- [x] Audit `type_time` and preflight as one evaluation contract: explain when work runs, what inputs it may depend on and how failures are caught. Exercise placement under bindings, calls, branches and handlers, and dependencies on effects. Prefer a uniform rule; justify each remaining context-sensitive exception with a required workload.
- [x] Audit the type/class/configuration boundaries: membership, realization, subtyping, fixed values, constraints and drift/convergence. Demonstrate which questions genuinely differ and consolidate overlapping mechanisms without conflating those questions.
  The [boundary decisions and migrations](LANGUAGE.md#classes-machines-and-drift)
  distinguish closed membership, completed configuration, conservative inclusion
  proofs and observation comparison. `convergence_policy` applies membership and
  planning to the same missing, changed and extra-field values and retains an
  independent 128-field comparison oracle.
  Construction and membership share provenance-kind and constraint-shape validation;
  realization and membership use the same field checker and predicate engine.
  Proofs and inheritance reuse exact retained-value identity where choosing a
  representative can affect behavior; public numeric/value equality is unchanged.
  `class_merge_identity` prevents parent order from choosing distinguishable values
  and covers explicit resolution and priority; `predicate_identity` covers retained constraints and reconstruction.
  Reconstruction preserves the complete constraint list and checks inherited
  requirements through the existing subtype relation instead of subtracting predicates.
  `class_constraints_realized`, `class_provenance`, `subtype_laws`, `type_algebra`
  and static checks cover invalidation, default/pin distinctions, declaration errors,
  exact captures and bounded numeric equality-class proofs. `provider_contract`
  independently verifies malformed schemas fail before observation effects.
  Full algebra proofs/completeness, arbitrary edited-descriptor certification and
  empty-plan equivalence to membership remain separate unresolved promises; this
  audit establishes the implemented boundaries, not those broader claims.
- [x] Audit builtin and standard-library responsibilities against actual callers. Remove redundant convenience paths where ordinary composition is clear; retain primitives where semantics or measured costs require them. Namespacing or relocating code alone does not close this item.

  | Boundary | Decision and executable evidence |
  |---|---|
  | Evaluation, functions and machines | Retain runtime scheduling, lazy forms, captures, predicates, immutable stepping and effect requests. Remove the unused `closure` builtin and its separate scope rule: `let` plus `fn` preserves capture and laziness. `closure_retirement`, `semantics`, `execution` and `type_time` exercise the remaining engine; `yaml_model` covers legacy migration. |
  | Ordinary values and data formats | Retain representation access, exact numeric operations and format grammars in Rust. Libraries compose collection, testing and configuration policy; YAML shares the graph parser/emitter, while JSON/CSV retain distinct grammar and encoding contracts. Registry-driven `semantics`, data-format goldens and library tests supply caller evidence. No measured basis justifies replacing these primitives. |
  | Type construction and validation | Keep the shared type/field parser, predicate requests and conservative proofs in the runtime; `std:type_descriptor` assembles values and `std:types` derives declarations. `with_field` now validates surviving fields without rejecting repair of the discarded field or executing predicates. `descriptor_policy`, `types_with_field` and `type_algebra` cover source blame, reconstruction and proof boundaries. |
  | State planning and application | Keep `std:diff`/`std:convergence` planning, `std:dependency` ordering, provider lifecycle, command guards and nginx activation in ordinary Helix. Their questions differ from type membership. Provider review compares canonical data encodings to preserve numeric representations; real-file `provider_contract` controls and `provider_review_identity` prove rejection before mutation. Command and nginx acceptance retain independent host postconditions. |
  | Development policy and supervision | Keep suite planning/reporting and golden actions in Helix. Static checking reuses bounded structural type rules; Python independently validates observed failures, requested authority and golden actions before writes. Production policy mutations, hostile `harness` responses and golden replay controls independently reject tested false-success and unauthorized-write responses. Bootstrap and process supervision remain independent. |

  Existing helpers already share the relevant authority: `is`/`expect`/`violations`
  adapt one checker, class inheritance reuses extension/realization, validation
  delegates table assertions, orchestration delegates dependency ordering, and
  command resources delegate provider lifecycle. Similar projection helpers retain
  their distinct missing-field contracts rather than being falsely unified.
  The removed `closure` form has no production caller; the [language migration](LANGUAGE.md#functions-and-local-names)
  documents `let` plus `fn`, ordinary user-defined names and offline legacy conversion.
  This audit does not claim faster execution: repeated descriptor construction
  retains growing-map copying and validation costs, and exact review adds bounded
  encoding work. Future optimization needs measurements of actual callers; more
  policy migration alone is not a benefit. General provider platforms, distributed
  recovery and formal type-algebra completeness remain separate obligations.
- [x] Evaluate representative source for readability: a small script, a higher-order transformation and the selected defining workload. Record where YAML wrappers or implicit evaluation rules obscure intent, try existing composition first and compare any proposed alternative before adding syntax.
  The tested arithmetic example exposes nested call wrappers, but ordinary named
  intermediates suffice. Production `tooling/test_policy.yaml` already names the
  rerun selection stages and processed report results; this preserves catalog
  order and avoids lexical self-shadowing. Callback nesting and differing
  `map`/`std:list.flat_map` argument order remain reading costs, not evidence for
  another syntax. `nginx_planning` names configuration and observations; the
  workload's `emit` callback confines eager-list effect sequencing. Separate
  resolve/plan/apply calls preserve review freshness and authority boundaries.
  Existing bindings and callbacks cover these cases; no syntax expansion is
  justified. Golden planning, policy harness and static mutation checks retain
  the executable evidence.
- [x] Separate the core language reference from library catalogs and host-operation manuals, retaining discoverable links and one authoritative description per contract. A basic program should not require learning provider lifecycles or command retry policy.

Core semantics now live in `LANGUAGE.md`, library contracts in `LIBRARY.md`
and host-operation contracts in `HOST.md`. README starts with the runnable
arithmetic example and links all three references; old language-reference
anchors redirect to the authoritative sections. The static suite validates local
manual links and exercises missing-file and missing-anchor failures. Basic source,
production test-policy callbacks and reviewed nginx application have distinct
navigation paths without duplicate contract descriptions.

### Evidence that consolidation helped

- [x] For each completed consolidation, show the same required workload before and after, identify the rules or competing paths removed and run the relevant existing acceptance checks. Include realistic resource observations when the change affects cost. Report remaining complexity plainly; line counts and builtin counts are supporting observations, never success criteria by themselves. The Docker nginx decisions and qualification above satisfy this checkpoint; the requirement continues for later consolidations.
- [x] Reconcile this charter and `AGENTS.md` after consolidation: remove superseded ambitions and duplicate obligations, preserve unresolved promises and make the next active target obvious. Keep operational detail in the tooling and reference documentation.

---

## Open questions

Resolve these only when a concrete workload depends on them; section 12 still
controls admission.

- [ ] What concrete monadic composition and transport/restoration protocol lets
  independently hosted participants preserve the reversibility promise? Section 2
  records the design direction; independent restoration remains unqualified.
- [ ] How should secret-bearing observations and external secret managers extend
  the current opaque-input boundary without persisting plaintext?
- [ ] Which real migration workload justifies the next provider/platform scope,
  and what recovery and target-coordination guarantees does it require?

Actor declarations, explicit effect yield, opt-in annotations, type-time regions,
structural class membership and merge rules are decided in
[LANGUAGE.md](LANGUAGE.md). Executable persistence uses versioned snapshots;
inspection and pure failed-run recovery remain separate formats. Remaining type
proofs, richer algebra and composition experiments stay in section 8.
