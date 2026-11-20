# Helix Charter Cockpit

**Goal:** Helix is a language I can reach for every day, for real scripts and automations, without apologizing for it.

The test for "daily driver" is simple: I start a task in Helix instead of Python or shell, I finish it, and I can open the saved state afterwards and understand exactly what happened.

This charter records direction, open acceptance criteria and the bounds of completed work.
[AGENTS.md](AGENTS.md) owns project architecture and current priorities;
[LANGUAGE.md](LANGUAGE.md), [LIBRARY.md](LIBRARY.md) and [HOST.md](HOST.md)
own implemented contracts. [tests/README.md](tests/README.md) owns test operation
and the evidence map; [CHANGELOG.md](CHANGELOG.md) and Git history own how
completed work got there.

**Completion rule:** check an item only when its stated scope works end to end
and has automated evidence through `./manage.py`. Language features need regression
coverage of their observable contracts: use goldens in `tests/cases/` for exact
serialized output and focused assertions for semantic values. Host behavior needs
independent host observations.
A demo or a `tests/programs/` fixture alone does not qualify. Parents stay open
while any stated obligation remains open. Tests do not establish formal proofs
or real-world adoption; those milestones need their own evidence.

**Compaction rule:** a completed element shrinks to its contract, its bounds and
the names of its evidence (suites and golden cases, discoverable with
`./manage.py test --describe`). Stated limits are never compacted away: what a
completed element does *not* establish stays written here.

**Direction:** declarative machine configuration through first-class types.
Resolve and validate desired configuration purely, then observe, review and apply
through caller-authorized effects. Daily scripting and readable saved execution
must make that foundation useful in practice.

**Audited status (2026-11-16):** sections 0–7 are complete within stated bounds:
core language, bounded reversible machines, host adapters, CLI/editor tooling
and runtime trust contracts. Versioned snapshots restore execution;
caller-owned journals refuse uncertain effect replay. Qualified workloads: Docker
nginx locally and over SSH with controller-death recovery; typed Debian resources
(`std:linux`) including two mutually configuring container peers; typed Ubuntu
facts. The type algebra has intersections, tuples, typed map keys, function
signatures, purity contracts and named recursive types, with a bounded static
checker that infers within a documented fragment. None of this establishes
general machine application, a real playbook migration, secret-safe arbitrary
data, remote fencing, fleet recovery, independently distributed reversibility,
formal type-system proofs or daily-driver adoption.

**Current focus** (AGENTS.md owns the live task list):

1. Measured development feedback on production policy workloads; harden the
   qualified Linux/Docker workloads. A real playbook migration must declare its
   supported scope before any provider expansion (section 11).
2. Derive, enforce and inspect closed / explicitly bound / open dependency
   boundaries for a concrete workload (section 2).
3. Extend the type algebra only where a workload needs it (section 8).

Everything else open in sections 8–11 is a deferred possibility, not an
automatic next task. Building a UI is out of scope; execution remains accessible
through APIs and CLI tooling.

**Admission rule:** section 12 governs every addition and policy migration.
Complete useful workloads and simplify existing semantics before opening another
feature family. Completed audits do not retire these continuing rules.

---

## 0–1. Foundation and core language — complete

YAML representation graphs retain parent links, source spans and root-relative
paths. Explicit `!call`/`!ref` tags use lexical lookup; `!literal` protects arbitrary data. Scalar style and mapping
order do not affect execution; cycles are detected and diagnostics are
structured. Finished ordinary-data roots round-trip through YAML.

Values, checked operators, lazy control forms, bounded iteration, YAML function
declarations, lexical closures and recursion with a clean depth-limit error are
specified in `LANGUAGE.md`, without silent coercion.

## 2. State you can read and resume — complete within bounded runtime contracts

If this is not great, there is no reason to use Helix over anything else.

Reversibility is a promise type. A machine that mixes it in must expose all
behavior and complete execution state (stack, heap, bindings, continuations,
iteration progress, pending effects) so it can always be serialized and restored;
hidden host state cannot carry part of the continuation. The runtime enforces
this through named, composable `promise` types and immutable `mixin`
application; `reversible` is the registered obligation. A transition that would
lose the previous continuation is refused. Promised machines refuse ordinary host
dispatch and arbitrary effect replies. `PromisedSystem` composes promised
participants through atomic `peer_send`/`peer_receive` rendezvous in one
checkpoint; **the complete system is the restoration boundary**.

Completed, with evidence in `snapshots`, `library`, `execution`, `memory` and the
`promises`/`yaml_model` goldens:

- Explicit `snapshot`/`restore` continue complete bounded execution in fresh
  processes, from the checkpoint and a compatible runtime alone (original source
  and imports deleted). Format version 2 is written as canonical block YAML and
  JSON checkpoints still load; version 1 migrates explicitly; unsupported
  versions are rejected. Future incompatible changes need a migration or
  documented rejection, and must extend the committed fixtures.
- All five statuses (`ready`, `running`, `finished`, `error`, `signaled`)
  round-trip. Positions persist as source-qualified root-relative paths; object
  names preserve sharing within one checkpoint, not global identity.
- Save/resume at every transition equals uninterrupted execution for the bounded
  nested-call, iteration, annotation, refinement, handler and child-machine programs.
- Editing a suspended live parameter resumes to a different result. Pure
  continuation replay is deterministic; future effect replies are external inputs.
- Joint promised systems keep exact delivery receipts, idempotent duplicates and
  both participants on failed delivery.
- Latest evaluated-value evidence carries producing paths/spans in bounded,
  explicitly truncated summaries. This is not a per-invocation trace.
- Canonical key order, byte-stable zero-step restoration and bounded transition diffs.
- YAML boundaries: bounded acyclic aliases and standard tags supported; cyclic
  aliases, unknown custom tags and non-string keys rejected.

Standing decisions:

- **Edits are cosmic rays.** Externally edited state simply appeared: continue
  from it without invalidating or recomputing completed work. Retained provenance
  describes recorded execution and does not certify edited values. Structural
  snapshot validation still applies.
- **Monadic composition** is the direction for independently hosted reversible
  computations. Representation, sequencing laws and transport/restoration
  protocol need a workload and evidence. It does not by itself establish
  agreement about already-delivered messages; the joint boundary stays in force.

Not established: external rollback, secret-safe serialization, distributed
rollback, remote fencing, independently restored remote participants (see
[beyond the joint restoration boundary](#beyond-the-joint-restoration-boundary--open)).

### Machine dependency closure and portability — open

Distinguish captured execution state from the external environment future
effects need. Immutable storage may be shared between retained versions;
exporting a machine must materialize all reachable code, values, captures and
continuations without the original process. Snapshot validation checks encoded
structure; completeness also needs implementation review and restoration
evidence. The boundaries below are design direction, not implemented
classifications:

- **Closed:** execution depends only on captured code and input data.
- **Explicitly bound:** execution additionally requires declared external
  resources; the destination supplies bindings and fresh permissions.
  Requirements carry no authority, and binding a resource reproduces neither its
  contents nor identical future observations.
- **Open:** execution can discover further dependencies, including through
  arbitrary host commands. Grants authorize operations but cannot enumerate what
  those operations use. Declarations alone do not establish closure.

Classification follows reachable capabilities and enforced boundaries, never an
author's assertion. Retained effect outcomes preserve recorded computation, not
the environment of future effects: restoration alone establishes neither
environmental reproducibility nor external rollback. Capturing an operating
environment is a separate contract.

- [x] Pure portability workload: a suspended machine finishes in another
  directory and fresh process from its checkpoint alone (`snapshots`).
- [ ] Derive and enforce dependency classifications for a concrete workload before
  claiming closed or explicitly bound portability.
- [ ] Expose an inspection report of captured inputs, pending external requirements
  and open boundaries, reusing snapshot and capability representations instead of
  an independent dependency inventory. Incomplete knowledge stays explicit,
  including dependencies discovered only by future commands.

### Beyond the joint restoration boundary — open

The joint `PromisedSystem` checkpoint is the only established multi-participant
restoration boundary. Four obligations lie beyond it. They are independent:
evidence for one establishes none of the others, and none implies external
rollback or exactly-once execution. Open an obligation only for a concrete
workload that needs it (section 12); the others stay deferred.

- [ ] Admission, required before work on any obligation below
  - [ ] Name the workload, the obligations it needs and those it leaves deferred
  - [ ] State the failure model: crash points, partitions, duplicated, delayed or
    reordered messages, stale controllers and nonparticipating writers.
    Unmodeled failures are outside the contract and documented as such.
  - [ ] Qualify on a disposable multi-participant fixture behind `./manage.py`,
    injecting each modeled failure at every commit and dispatch boundary and
    observing outcomes independently of Helix's own reports
- [ ] Independent remote restoration: one participant restores without the rest
  - [ ] Representation and sequencing laws for monadic composition of
    independently hosted promised computations, checked by law tests
  - [ ] Delivery agreement: a restored participant and its peers agree on which
    messages were delivered; a restoration contradicting a peer's receipts is
    refused, never silently reconciled
  - [ ] Participant identity and epoch, so a stale or duplicated restored copy
    is detected before it communicates
  - [ ] For the workload, independent restoration reaches the state joint
    restoration would, or is refused
- [ ] Remote fencing beyond one cooperating lock domain (bound today:
  [target lock](HOST.md#coordinating-one-target-across-journals))
  - [ ] A caller-authorized coordinator issues leases with monotonic fencing
    tokens; source, inventory and restored state confer none
  - [ ] The target, not only the controller, refuses mutations carrying an
    expired or superseded token
  - [ ] A partitioned controller stops before its next mutation; in-flight
    requests stay uncertain and are reconciled explicitly
  - [ ] Nonparticipating writers are detected by fresh observation and review
    matching, never assumed absent
- [ ] Fleet recovery: section 11's fleet orchestration and recovery list. It
  composes per-host journals and target coordination; a fleet record never
  replaces them.
- [ ] Secret-bearing ordinary data (bound today: opaque caller-granted handles,
  section 11)
  - [ ] Secret-derived values never serialize as plaintext: snapshots, journals,
    inspection, diagnostics and provenance summaries retain only handles or
    redactions, and the propagation rule is stated in `LANGUAGE.md`
  - [ ] Restoration requires the caller to supply secret inputs again
  - [ ] Canary secrets through every serialization path, verified independently

## 3. Talking to the world — complete within supported adapter bounds

Caller-authorized file I/O, arguments/exit codes, environment, standard streams,
command capture, HTTP, time/randomness and JSON/YAML/CSV codecs. Pending effects
are explicit machine state; host events interrupt and resume in-memory
execution. Evidence: `effects`, `permissions`, `execution`, `semantics`,
`long_commands`, `durable_effects` and the `effects_world`/`effects_pending`/
`std_codecs` goldens.

Bounds: files and streams are UTF-8 text; HTTP is GET through installed `curl`.
Retained branches need explicit host handling and share no global effect
deduplication. Caller-owned journals preserve completed effects and require
explicit reconciliation of uncertain requests; this is not exactly-once external
execution. Distributed recovery and effectful reversibility beyond section 2's
bounded promise remain open.

## 4–5. Errors and reuse — complete

Structured errors are readable data with paths, spans, call chains and retained
causes. `try`, kind-matched recovery, `raise`, assertions and `finally` support
independent validation and cleanup; caught errors stay visible in saved roots.
Pure recovery checkpoints preserve completed root fields and restart unfinished
calls after correction; executable restoration is section 2's separate format.

File imports provide isolated namespaces, documented visibility and
circular-import diagnostics. Dependencies are repository-pinned vendored paths
(`imports`, `import_vendored`).

## 6. Daily tooling — complete within current CLI/editor scope

`run`, REPL, canonical formatter, non-executing `check`, step/trace, saved-state
inspection, content-based watch, JSON Schema/LSP, shell completion and
`cargo install`; `std:test` supplies composable in-language assertions, comparisons
and reports used by golden checks. Evidence: `interactive`, `inspection`, `static`, `cli`.

Bounds: plain `check` neither evaluates nor opens imports; value-dependent
failures belong to `check --evaluate`. Inspection reads saved states and
inspection snapshots, not restorable continuations. File discovery and process
supervision remain host duties.

## 7. Trust — complete within current runtime bounds

Performance and bounded memory (`scale`, `speed`, `memory`, `resources`), parser
and evaluator fuzzing (`fuzz`, `robustness`, `mutants`), a capability model for
current effects (boundaries, not OS isolation), a stability promise, language
reference, tutorial and broad-strokes changelog.

Continuing obligations:

- Testing is a substantial engineering responsibility. New formats, adapters and
  platforms extend the evidence before claiming support. Every suite is
  reproducible through `./manage.py`. Scenario counts are not branch coverage or
  proofs; the Rust/Python size ratio is a prompt to inspect gaps, not a target.
- Persistence: a snapshot format change extends the committed compatibility
  fixtures and the save/resume-at-every-transition comparison.

---

## 8. Types: declarative, first-class, and the machine-config niche

The long-range target. A type is ordinary readable YAML, a value like any other,
and a machine config is a type rather than a script: everything about it is
known and checked at type time, before any effect touches a machine.

Principles that settle most design questions in this section:

- **One language, one graph.** Types live in the same object graph, YAML and
  evaluator as programs and state. No schema dialect, template layer or generics
  syntax. If a feature needs a second notation, the design is wrong.
- **Readable types, explicit persistence boundaries.** Ordinary type data can be
  read, diffed, edited and re-read. Predicates keep executable captures only
  through versioned snapshots; inspection graphs are not executable source.
- **A type describes, it does not act.** It never converts, coerces, fetches or
  repairs; checking passes or yields error data.
- **Wrong is loud and early.** Move failures from "halfway through changing a
  machine" to "before anything ran", all at once, each blamed on a node.
- **Explain where things came from.** "Who set it and why did it win" is answered
  from the state, not from a debugger or a precedence table in the reader's head.

```yaml
has_ssh: !call [class, {ssh_port: {type: port, default: 22}}]
server: !call [class, {hostname: string, packages: !call [list_of, !ref string]}]
web01: !call [inherit, !call [server, !ref has_ssh], {hostname: web01, packages: [nginx]}]
```

### Declarative and first-class types — complete within documented bounds

`LANGUAGE.md` owns the rules; `type_algebra`, `type_time`, `static`,
`execution`, `errors` and the `types_*` goldens own the evidence.

- Types are plain YAML source nodes: scalars, `any`, lists, maps, closed records
  (required/optional/defaulted fields), unions, enums, literals and stackable
  refinements whose predicates are ordinary `fn`s. Declarations are
  order-independent; malformed declarations blame the declaration.
- Opt-in annotations on values and `fn` parameters/results. A mismatch is a
  structured `type` error with expected label, actual value and inner path.
  Unannotated programs keep their semantics.
- Every violation of one value is collected in one pass, in a documented stable order.
- Types are values: bound, passed, returned, stored, compared with `eq`. Type
  constructors are ordinary functions. Reflection reads fields, defaults and
  parents as data, and reflected types re-read as the same type.
- Type time is ordinary evaluation under a pure, bounded contract. An attempted
  effect is a structured error; closed regions preflight before the first
  run-time step; recorded `type_time_inputs` name run-time values read.

Bounds: records are closed. Saved annotation evidence is bounded and may be
truncated. Inspection is not executable restoration. Regions inside `try`, a
`vm` thunk, an unreached function or reading a parameter keep ordinary order.

### Static checking and inference — complete within the documented fragment

`helix check` verifies a stated static fragment without executing
(`LANGUAGE.md`, "Static fragment, inference scope and guarantees"; `static`):
annotated function bodies, both conditional branches, and inferred expression,
binding and unannotated function-result types. It reuses the authoritative
subtype rules instead of a competing semantics. Anything outside the fragment
is reported `unchecked`, with analysis-budget exhaustion reported separately,
never passed by assumption. Runtime checks remain for those obligations: a
clean static report is not proof of successful execution, and `type_time` does
not prove unexecuted code safe.

### Expanding the type algebra

State the meaning of each operation before choosing its syntax. Membership,
subtyping and equivalence are different questions. Every new former must compose
with existing types, reflection, diagnostics and the YAML round trip.

Complete (`types_bottom`, `types_intersection_tuple`, `types_function_signatures`,
`types_function_purity`, `types_recursive`, `type_laws` in `type_algebra`): top
`any` and bottom `never`; intersections with exact rules for
`T <: intersection` and `union <: T` and a sound-only `intersection <: T`;
fixed-length tuples; typed map keys; `function_of` with contravariant
parameters and covariant results; `pure_function_of` and static purity of code
reachable from `type_time`; guarded named recursion through an explicit
`rec`/`var` binder; `subtype` and `equivalent` (mutual subtyping) kept separate
from structural `eq`; inherited class constraints combined by intersection;
default selection kept separate from membership.

- [ ] Difference, complement and negation, only within a defined universe and a
  documented decidable fragment
- [ ] Record row constraints and explicit width/depth subtyping rules
- [ ] Refinement implication and constraint composition: which facts are
  decidable, need evidence or stay unknown
- [ ] Normalization that keeps original declarations and provenance; state when
  it is canonical and meaning-preserving
- [ ] For every former: documented membership, subtyping, equivalence,
  reflection, serialization and failure behavior, with positive, negative and
  boundary golden cases

### Strengthening and proving the type system

Justified guarantees with visible assumptions. Examples and randomized checks
find counterexamples; they are not proofs. Arbitrary refinement predicates make
general implication undecidable, so a bounded checker must distinguish proved,
disproved and unknown, never treating exhausted resources as an answer.

- [ ] A small formal core (syntax, value/type domains, evaluation, typing and
  effect judgments), stating which implemented features it covers
- [ ] Algebraic laws for the supported fragment: subtype reflexivity/transitivity,
  equivalence, union/intersection bounds, commutativity, associativity,
  idempotence, absorption, top/bottom identities
- [ ] Membership and subtyping algorithms sound against the core; completeness
  only for identified fragments
- [ ] Decidability and resource bounds per operation; unknown or exhausted checks
  are structured results distinct from rejection and malformed declarations
- [ ] Normalization preserves denotation and terminates; confluence or uniqueness
  if canonical forms are promised
- [ ] Preservation and progress for the typed core, including structured errors,
  effects and the unannotated boundary
- [ ] Type-time evaluation performs no run-time effects and is deterministic under
  its declared inputs; separate resource-bounded failure from termination
- [ ] Inherited constraints survive composition and parent order cannot change a
  successful result; defaults and override conflicts under their own rules
- [ ] Serialization/reflection preserve meaning, including snapshots of suspended
  execution and captured predicates
- [ ] Mechanize the core in a chosen proof assistant, recording assumptions,
  trusted components and the correspondence with the Rust implementation
- [ ] Reproducible algebra-law, generated-counterexample and differential checks
  through `./manage.py`, with minimized failures kept as regressions
  - [x] Subtype laws over a generated type pool (`type_laws`)
- [ ] Proof checking discoverable through `./manage.py`; a changed semantic rule
  updates proofs and executable cases together

### Type classes and inheritance

An abstract class is an incomplete description: fields fixed, defaulted, or
demanded and left open. Inheriting composes descriptions; a concrete type has
nothing open. Classes are small and combined freely rather than arranged in a
deep tree. The merge rules are the heart: few, written down, deterministic, and
they refuse to guess. An unresolved ambiguity is an error, never a quiet winner.

Complete (`LANGUAGE.md#classes-machines-and-drift`; `class_*`,
`types_class_membership`, `classes`, `dogfood_class_conflict*` goldens):
fixed/defaulted/open fields and whole-record constraints; single, multiple,
diamond and deep inheritance; structural membership (parents drive composition
and provenance, not nominal identity); agreement-or-explicit-resolution merges;
child values override parent defaults and differing parent defaults conflict;
whole-value collection replacement with explicit `concat`/`std:map.merge`;
parent order never changes the result; every parent constraint holds on the
realized result; all missing fields listed at once with the demanding class;
provenance queryable as data; parametric classes as ordinary type-checked `fn`s.

- [ ] A class is a type value: everything under first-class types applies to it
- [ ] The inherited result emits as a plain flattened type that reads as hand-written
- [ ] Conflict errors give the field and the path and line of each disagreeing
  declaration (today: class names), with every conflict reported in one pass
- [ ] An override violating a parent's field type or constraint blames the
  override and names the parent
- [ ] Provenance records each field's source as a root-relative path with line,
  shows what lost and to what, records parametric instantiation arguments,
  round-trips, and does not make the resolved type harder to read or diff

### Composition over inheritance — deferred

Take the Smalltalk intuition with a small pure core: an object is readable data
with behavior, and message application is a function of receiver and arguments.
These are workload ideas, not a commitment to object syntax or dispatch. First
demonstrate where ordinary maps, explicit receiver arguments and functions fall short.

- [ ] Objects from immutable records and existing first-class functions; message
  application is explicit receiver/name/arguments lookup with one documented
  `self` binding rule; transitions return new values
- [ ] Explicit capability composition, delegation and adaptation (select, rename,
  exclude): disjoint members combine, shared members need agreement or explicit
  resolution, no hidden superclass chain
- [ ] Polymorphism through a shared message contract checked by the type algebra;
  behavior inspectable as data with structured errors for missing or conflicting messages
- [ ] Composed behavior survives versioned snapshots; composition laws (identity,
  associativity, conflict behavior) documented with golden cases; one small pure
  object demonstrated without inheritance or a parallel method evaluator

### Machine configs as type-time types

A typed desired machine description, resolved without machine contact before
observation and application. The implemented operations stay distinct: class
realization fills defaults and validates; membership checks allowed values;
convergence compares configured values with observations and preserves unmanaged
extras. **An empty plan is not membership in a closed class.** Each supported
workload must establish freshness, idempotence and recovery with independent
observations.

Complete within bounds: `std:machine` vocabulary (`std_machine`); pure
`drift`/`converge` over supplied data emitting canonical field-ordered `set`
steps, with extra observed fields reported in `drift.extra` and never removed
(`machine_fleet`, `convergence_policy`); the nginx class realizes defaults and
constraints purely and rejects incomplete, invalid or effectful declarations
before observation, with `nginx.resolve` returning complete values and
`nginx.explain` adding provenance (`nginx_types`); opaque `secret(ref)` handles
resolved only while dispatching a consuming effect (`secret_effects`,
`ssh_host`). Section 11 owns application, recovery and secrets in operation.

- [ ] A machine config is a concrete type inheriting from abstract machine
  classes, fully resolved at type time
  - [ ] An unfilled or conflicting field fails at type time with the class errors above
  - [ ] No machine or network contact during resolution, enforced by the
    type-time contract rather than convention
  - [ ] Machine-only facts are typed inputs to realization, never read during
    resolution (bounded evidence: `host_facts`, section 11)
- [ ] The resolved config serializes to a complete flattened description that
  diffs well: stable order and formatting, a one-field class change is a one-line
  change per inheriting machine, provenance separable or opt-in
- [ ] Reproducible resolution: pinned imports recorded in the resolved config,
  Helix and state-format versions recorded, byte-identical output on two machines
- [ ] Fleet-level type-time checks: a fleet is a typed collection of configs,
  constraints are ordinary predicates, a violation names every machine involved
  with paths, all violations in one pass
- [ ] Realization as an explicit step producing a plan as data from a typed
  observed state; any machine-satisfaction relation beyond convergence needs its
  own contract
- [ ] Applying a plan: each step is an explicit effect recorded before it runs,
  resumes from the pending step, is idempotent, and leaves a fresh plan empty
- [ ] Drift in both directions as data, per field, with the type's provenance
- [ ] Secrets never appear in resolved configs, saved state, errors, plans or
  drift reports (section 11 owns the operational obligations)

---

## 9. Dogfooding milestones

Real programs, grouped by what unlocks them. Each is a checkbox only when I
actually use it, not when it merely could work. Golden coverage is evidence of
executable examples, not adoption. Write down what was awkward: that friction is
the real roadmap, but not every awkward expression is a feature obligation.

### With the pure core
- [ ] Classic small problems in real use (executable: `dogfood_classics`)
- [ ] A 100+ line program written without fighting the language (executable: `dogfood_work_queue`)
- [ ] One real data-reshaping task: paste YAML in, compute a report, use the emitted `root`
- [ ] Review a real change from a configuration diff report (executable: `dogfood_config_diff`)
- [ ] Fix real inventory data from a validation report (executable: `dogfood_project_inventory`)
- [ ] Order a small set of real tasks, recovering from missing and cyclic dependencies (executable: `dogfood_dependency_order`)
- [ ] Correct real inventories or expense lists from reconciliation findings (executable: `dogfood_expense_reconciliation`)
- [ ] Turn friction from actual programs into one concrete improvement or a recorded deferral

Recorded friction: tokenization folds over character slices; nested changes
report at the top-level key; immutable removals and insertion sort copy
quadratically; currency units and matching keys are user conventions; lazy
bindings need assertions in the result dependency chain. `dogfood_errors*`
covers validation, recovery, retry histories, cause wrapping and cleanup without
establishing external retry or resource-management guarantees.

### With suspend/resume (section 2)
- [ ] A long computation stopped midway, its state read in `git diff`, resumed to the same result
- [ ] Resume a run that stopped on an error after fixing its cause by hand, without redoing preceding work
- Executable in `snapshots`: live-parameter repair, stopping inside nested calls
  and iteration, relocation without source, and diffing two retained branches.

### With effects and imports (sections 3–5)
- [ ] Replace one small shell script with Helix
- [ ] A config-transform pipeline (read YAML in, compute, write YAML out)
- [ ] Audit real project files for broken local links and repair them from the report
- [ ] Turn a real CSV export into a summary used in a recurring report
- [ ] Generate a changelog draft from git history, source commits retained in the saved state
- [ ] Call a paginated HTTP API, save progress between pages, resume after a failed request
- [ ] A batch file transformation with preview, explicit apply and an undo manifest
- [ ] Share one imported helper between two real scripts
- [ ] Recover a failed external-command workflow without repeating completed steps
- [ ] A task runner for this repo (`./manage.py` logic expressed in Helix)
- [ ] A long-running workflow that suspends on an external event and resumes days later
- [ ] A program that inspects and edits another Helix program's saved state
- [ ] Helix running its own test suite

### With daily tooling and trust (sections 6–7)
- [ ] Diagnose a real failed script from its saved state and turn it into a regression case
- [ ] Run an unfamiliar program with restricted permissions, granting only what it needs
- [ ] Upgrade Helix and resume a state saved by the previous version
- [ ] Someone else installs Helix, runs and adapts a script using only the documentation

### With types and machine configs (section 8)
- [x] Declared types replace hand-rolled checks (`dogfood_project_inventory`);
  a type-returning function removes real duplication (`dogfood_derived_changes`);
  a deliberate parent conflict is fixed from the error alone (`dogfood_class_conflict*`)
- [ ] Model my own machines as types: a laptop and a server sharing abstract classes
- [ ] Review a config change as a `git diff` of the resolved type before anything is applied
- [ ] Manage one real piece of one real machine from its Helix type for a month
- [ ] Rebuild a machine, or bring up a second one, from its type alone; record everything done by hand

### The bar
- [ ] A week where I reach for Helix first without thinking about it
- [ ] Three recurring personal or repo workflows stay in Helix for a month; record each fallback and its reason

---

## 10. Super dogfood

Use Helix for substantive development work both to accomplish it and to strengthen
the language through real use. Awkward composition, missing library capabilities
and runtime limits are useful feedback even when the first implementation is longer
than host code. Develop ordinary reusable functions and compose them for the tool;
do not turn its fixed workflow into a public language capability. Migration ideas
are candidates, not architectural obligations. Success is useful responsibility and
language feedback, not code volume. Preserve an independent build bootstrap and
independent host failure detection.

Complete, each with a production caller and its replaced Rust policy removed:
`std:math`, `std:list`, `std:map`, `std:text`, `std:group`, `std:diff`,
`std:validation`, `std:dependency`, `std:type_descriptor` (descriptor assembly),
`std:convergence` (drift/plan policy), and test policy (`std:test`,
`std:fuzz.plan`, general `std:test` assertions and reports composed for golden
checks, and `tooling/test_policy.yaml` driving `./manage.py test` selection,
ordering, reruns and reports). Python captures executions, validates terminal
states, supervises ordinary-root replay and writes explicitly blessed expectations
after checks pass; CI never blesses. It consumes Helix check reports without
reimplementing their comparisons. Independent harness fault injection detects
invalid output, nondeterminism, mismatches and failed replay. Python also validates
suite-policy consistency and authority and detects interpreter failures. Immutable copying costs are quadratic in places and
documented in `LIBRARY.md`; these modules suit small inputs.

Deferred by section 12's boundary audit: moving the type checker or class
realization into Helix. Both need shared validation, predicate scheduling,
resource bounds and source blame; moving only assembly adds cross-language
sequencing. Reconsider only against a measured failing caller.

- [ ] Formatting/inspection policy in Helix, only if it improves on `src/emit.rs`:
  needs lossless scalar/source reflection, escaping support and a bootstrap
  output path that does not recurse into the formatter
- [ ] Helix graph passes for a concrete static-analysis workload; the Rust checker
  stays until a replacement shows a benefit. The `static` suite already checks
  production `std/` and `tooling/` sources and keeps unresolved obligations explicit
- [ ] Further test/task feedback improvements against a measured target; filesystem
  discovery, process supervision and build/lint invocation stay host-owned

Recorded friction: output-map fields shadow same-named enclosing bindings; lazy
bindings can repeat work in callbacks. Distinct names, `dict` and eager function
arguments address both without new evaluation rules.

**Migration bar:** move one complete responsibility at a time. The Helix module
becomes authoritative, production callers use it, and the replaced Rust policy
is removed; a forwarding wrapper does not count. Preserve public syntax,
structured errors, locations and state round trips, with parity shown through
focused assertions, serialized-contract goldens, malformed-input coverage and
realistic sizes. Never leave two
policy engines. Defer a self-interpreter or YAML parser rewrite until lossless
graph reflection, explicit continuations and bootstrap recovery make it a
concrete improvement.

---

## 11. Replacing Ansible

Replacement means operating real machines reliably within a declared support
scope, not invoking shell commands from YAML. Start with Linux hosts reached
locally or over SSH; expand from demonstrated needs. Playbook syntax
compatibility and collection parity are not requirements. A scoped replacement
must state what it cannot manage.

Section 8 owns configuration resolution and pure planning; sections 2–3 own
snapshots and host effects. This section owns operational capability and
acceptance evidence.

### Qualified workloads — complete within their operator contracts

Each claim is bounded by its operator contract; none is general machine management.

- **Docker nginx** (`docker_nginx`, `ssh_nginx`, `nginx_types`;
  [contract](tests/fixtures/docker_nginx/README.md), local and SSH qualification
  records beside it). One readable configuration installs nginx, manages
  configuration and content, and keeps it serving inside one digest-pinned
  Debian container. Qualified scenarios: clean preview, first application,
  repeated no-op, configuration and content updates, independent and combined
  drift, invalid configuration/input, stale review, missing authority, package/
  file/service failure, false-success commands, timeout/cancellation, controller
  death with fresh reconciliation, explicit removal and container lifecycle.
  Over SSH, a controller killed after a remote mutation is recovered by
  refusing uncertain replay and reconciling the exact request. Not established:
  systemd or boot persistence, concurrent controllers, multi-file transactions,
  rollback, arbitrary-host bootstrap.
- **Typed Debian resources** (`std:linux`, `docker_linux`;
  [contract](tests/fixtures/docker_linux/README.md)). Groups, users,
  exact-version packages (an installer layer keyed by manager; only `apt` is
  defined), directories, files and symlinks. Prerequisites derive
  into a validated, reviewed dependency graph; explicit `state: absent` removes
  in reverse order and omission never deletes; a failed resource skips exactly
  its transitive dependents. Two container-resident controllers configure each
  other over pinned SSH. Not established: services, boot persistence, templates,
  cron, firewall, concurrent-controller or fleet recovery.
- **Typed facts** (`std:facts`, `host_facts`, `docker_facts`).
  Versioned schema with explicit `unknown`/`inaccessible` states; caller-granted
  read-only, uncached, bounded and selectable collection; custom facts;
  consumed facts recorded in the review so a plan goes stale when they change.
  Qualified on Ubuntu 26.10/x86_64 only.
- **Inventory** (`std:inventory`, `host_inventory`;
  [contract](HOST.md#host-inventory)). Typed hosts, groups, selections and
  explicit overrides with provenance; dynamic inventory is a validated explicit
  input. Connection descriptions confer no authority.
- **Provider contract and composition** (`std:provider`, `std:command`,
  `std:orchestration`; `provider_contract`, `command_provider`,
  `resource_composition`). Typed observe/plan/apply/verify with exact fresh
  review matching, unsupported-preview gates, explicit ownership and removal,
  and re-observed postconditions; guarded commands with no implied idempotence;
  validated dependencies and coalesced handlers. Callbacks are trusted
  implementations; revision identity is author-declared.
- **SSH adapter** (`ssh_host`; [contract](HOST.md#ssh-remote-commands)). Strict
  host-key verification against caller-supplied `known_hosts`, non-interactive
  key/agent authentication, jump hosts, connection reuse, caller-granted
  `become`, byte-exact argument/stdin/file transport (UTF-8 text up to 1 MiB),
  and prerequisite check/bootstrap by observation.
- **Long commands, journals, locks** (`long_commands`, `durable_effects`,
  `target_coordination`). Caller-controlled deadlines, capture budgets, progress,
  classified retries and process-group cancellation. Journals distinguish
  undispatched, completed and uncertain requests; read-only `checkpoint`
  reports pending work. A target lock excludes cooperating controllers in one
  shared lock domain and survives controller death until acknowledged.

### Open: one real host

- [ ] Replace one real configuration playbook end to end: declare package, managed
  file and service; observe, plan, review, apply and independently verify; a
  second run mutates nothing and introduced drift gets only the necessary repair.
  Declare the supported scope first.
- [ ] Detect supported operating systems, package managers and service managers
  before selecting adapters
- [ ] Observations carry host identity and freshness; unknown or inaccessible
  state is never treated as absence, beyond the facts schema
- [ ] SSH connection, authentication, privilege and transport failures stay
  distinguishable without parsing OpenSSH diagnostics (needs a typed protocol boundary)
- [ ] An initial resource library behind the provider contract, beyond the pinned
  Debian scope above: package managers beyond `apt` under the installer layer
  (snap and flatpak need a systemd fixture), repositories (metadata refresh, locking);
  configuration rendering (deterministic templates, validation, reviewable
  diffs); services (enabled/running, reload/restart policy, readiness);
  authorized keys; artifacts (authenticated retrieval, pins, integrity)

### Open: fleet orchestration and recovery

- [ ] Define fleet recovery, healing and regeneration before implementing them:
  what each means, which failures and environments it covers, what state and
  authority must survive, and what independent evidence establishes success.
  Regeneration's scope is deliberately undefined.
- [ ] Bounded, observable fleet scheduling: concurrency limits, serial batches,
  canaries, failure thresholds; cross-host dependencies and delegated tasks with
  unambiguous targets and authority; health-gated rolling changes; reboot,
  disconnect and reconnect with bounded readiness waits
- [ ] Stale plans and conflicting controllers beyond one cooperating lock domain
  (remote fencing, partitions and nonparticipating writers: section 2)
- [ ] Declare compensations and irreversible operations explicitly; external
  rollback is never implied by immutable VM state
- [ ] Resume an interrupted multi-host run from one fleet-level record
  - [ ] Persist run identity, reviewed per-host plans, batch/canary position and
    journal locations; restoring supplies fresh grants and never replays uncertain effects
  - [ ] Classify every host as unstarted, applied, failed, skipped or uncertain,
    with the next action per host and for the run; never report a fleet
    successful because some hosts completed
  - [ ] Reconcile uncertain hosts independently, re-verify completed hosts by
    fresh observation, and continue only batches whose failure policy still holds
  - [ ] Detect hosts whose state or identity changed since review
  - [ ] Tolerate controller death between batches, an unreachable host on resume
    and a second controller on the same run; a lost fleet record degrades to
    per-host journals, never to silent re-application
  - [ ] Fleet status as concise text and versioned data with documented exit statuses
  - [ ] Qualify on a disposable multi-host fixture with controller kills at batch
    boundaries and mid-host

### Open: credentials, operator workflow, extensibility

Opaque caller-granted secret files already feed local/SSH command stdin and SSH
file contents, with captures suppressed before outcomes, snapshots and journals
persist (`secret_effects`). Ordinary plaintext inputs and later secret-bearing
observations are outside that boundary.

- [ ] No secret disclosure through command arguments, captured output, errors,
  diffs, logs, plans, drift reports or persisted state, verified with canary
  secrets in every channel
- [ ] Secret-manager adapters behind caller grants: a typed reference (provider,
  path/key, optional version) resolved only in the host at the apply boundary;
  distinguishable missing/denied/unreachable/malformed failures; reviews bind a
  non-secret version or digest so rotation makes them stale; no caching by
  default, explicit TTL and rotation behavior; previews resolve nothing. Qualify
  one disposable Vault/OpenBao container; other managers are separate work.
- [ ] Inventory data stays separate from authority: credentials, escalation and
  provider capabilities remain caller grants
- [ ] Discoverable resolve/observe/plan/apply/verify commands with target
  selection, per-host/per-resource reports as text and versioned data, documented
  exit statuses for CI, and a supported install/upgrade path with
  controller/managed-host compatibility requirements
- [ ] A versioned provider interface with capability checks, dependency pinning,
  conformance tests for third-party providers, a published support matrix and a
  migration guide stating unsupported semantics

### Evidence required before calling it a replacement

- [ ] Infrastructure tests behind `./manage.py` for every supported
  provider/platform combination against actual host state: clean install,
  no-op, drift repair, preview accuracy, unmanaged-state preservation; injected
  authentication, permission, staleness, network and controller-death failures
  at mutation boundaries; recovery, redaction, selection and thresholds verified
  independently of Helix's own reports; realistic fleet sizes against declared budgets
- [ ] Retire Ansible for a declared real workload after a documented migration
  trial: equivalent intended host state and operator-visible failures, routine
  changes and an interrupted deployment handled with Helix alone, and support
  claims kept aligned with tested implementation

---

## 12. Complexity backpressure and consolidation

Helix earns its complexity through readable programs, understandable execution
and completed real workloads. Small syntax, fewer Rust lines, more Helix code and
more checked boxes do not establish simplicity. Count the concepts a user must
learn, the interactions an implementer must preserve and the obligations an
operator must meet. Library APIs and data protocols carry these costs too.

### Admission and stopping rules

These apply continuously, including to features already listed above. Record the
reasoning in the change description and existing reference documentation; do not
create a parallel approval system or feature ledger.

- Every new public concept, builtin, evaluation rule or protocol must name a
  concrete failing workload, show the closest solution using existing
  facilities, and explain why it is insufficient. Hypothetical extensibility and
  parity with another language are insufficient reasons.
- Prefer ordinary functions and explicit composition. A new runtime mechanism
  must identify the semantic or measured resource requirement existing modules
  cannot meet. Moving an implementation into Helix does not by itself simplify
  its public contract.
- Explain the added obligations: user-visible rules, affected interactions,
  diagnostics, resource costs and compatibility. State what is removed or
  consolidated, or why a net increase is necessary. Do not force unrelated
  removals to satisfy a feature-count quota.
- Evaluation changes must account for lexical scope, laziness, memoization,
  error handling, effects, stepping and retained machines, with focused
  regressions through `./manage.py`; no second evaluator and no exhaustive
  combinatorial test project.
- When supporting machinery expands beyond the chosen workload, narrow or defer
  the feature. Correctness, security and diagnostic repairs remain necessary; a
  repair that introduces new semantics needs the same justification.
- Simplification must preserve required behavior or explicitly migrate it.
  Independent evidence includes process/state observations and harness fault
  injection; it does not require the host to recompute each Helix assertion or
  duplicate its decision policy. Test the boundary independently. Do not weaken
  failure detection, suppress diagnostics or omit limitations.
- Finish one defining workload before broadening. Replacing the active target
  requires recording why it failed to provide evidence and what is deferred.
  Completion does not authorize the rest of the roadmap: review which mechanisms
  the workload used, which complicated it and which remain speculative.
- Each consolidation shows the same workload before and after, the rules or
  competing paths removed, and the relevant acceptance checks. Line and builtin
  counts are supporting observations, never success criteria.

The golden-test migration exposed a boundary mistake: fixed harness workflows
became public `golden_plan`/`golden_decision` APIs and host vetoes repeated their
decisions. Moving all checks back to Python removed the dogfood instead of fixing
its design. Compose ordinary assertions and reports in Helix, retain independent
host supervision and harness tests, and revise established ownership boundaries
when they obstruct that simpler design.

### Decisions from the completed nginx consolidation

The first defining workload (Docker nginx) and its consolidation audit are
complete. The reference manuals and suites keep the detail; these decisions stand:

- Desired-state policy lives in `std:nginx`, provider lifecycle in
  `std:provider`, dependency scheduling in `std:orchestration`. Planning
  (`std:diff`, `std:convergence`) and type membership answer different questions
  and stay separate.
- `type_time` and preflight are one evaluation contract over the same pure
  engine ([phase contract](LANGUAGE.md#type-time-evaluation)); linked file and
  embedded modules evaluate through it.
- Membership, realization, subtyping and drift/convergence are genuinely
  different questions sharing one field checker and predicate engine
  ([boundaries](LANGUAGE.md#classes-machines-and-drift)). Full algebra
  completeness, certification of edited descriptors and empty-plan equivalence
  to membership remain unresolved promises.
- Runtime keeps scheduling, laziness, captures, representation access, numeric
  operations, format grammars, the shared type/field parser and conservative
  proofs. Libraries own collection, testing, descriptor-assembly and
  configuration policy. The unused `closure` builtin is removed: `let` plus `fn`
  covers it.
- Provider review compares canonical data encodings. Activation claims require
  fresh observations and exact responses. Atomic file replacement is partial
  progress, not rollback.
- Core semantics, library contracts and host operation live in `LANGUAGE.md`,
  `LIBRARY.md` and `HOST.md` with one authoritative description per contract; a
  basic program requires no provider or retry knowledge.
- No syntax expansion is justified by the readability review. Retained friction:
  lexical fields can shadow outer names, YAML call wrappers are verbose,
  callback argument orders differ, and review/phase/effect metadata needs
  careful reading.
- No performance claim: repeated descriptor construction keeps growing-map
  copying costs and exact review adds bounded encoding work. Optimization needs
  measurements of actual callers.

- [ ] Migrate `std:nginx` onto the shared `std:helper` framing that `std:linux`
  already uses. Its goldens and qualification digests are pinned, so this is a
  deliberate consolidation with requalification, not incidental cleanup.

---

## Open questions

Resolve these only when a concrete workload depends on them; section 12 controls
admission.

- [ ] How should secret-bearing observations and external secret managers extend
  the opaque-input boundary without persisting plaintext (section 11)?
- [ ] Which real migration workload justifies the next provider/platform scope,
  and what recovery and target-coordination guarantees does it require?

Decided in [LANGUAGE.md](LANGUAGE.md): actor declarations, explicit effect
yield, opt-in annotations, type-time regions, structural class membership and
merge rules. Executable persistence uses versioned snapshots; inspection and
pure failed-run recovery remain separate formats.
