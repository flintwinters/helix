# Helix Charter Cockpit

**Goal:** Helix is a language I can reach for every day, for real scripts and automations, without apologizing for it.

The test for "daily driver" is simple: I start a task in Helix instead of Python or shell, I finish it, and I can open the saved state afterwards and understand exactly what happened.

How to use this file: tick a box only when the feature works end to end *and* has a golden case in `tests/cases/` (run `./manage.py test`). A feature that only works in a demo stays unticked. Edit freely; this file is for humans, while `AGENTS.md` holds the agent-facing architecture and invariants.

**Where it is headed:** the niche Nix and Ansible occupy, declarative machine configuration, approached through types. A machine config is a type, fully resolved at type time and inheriting from abstract machine type classes; section 8 holds that road. Daily-driver scripting comes first because the config language has to be a language worth using.

**Where we are:** a pure, total-ish expression language (values, operators, control flow, functions, iteration) that runs to completion; no suspend/resume, effects or types yet.

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
- [ ] Error values carry a call chain with paths and source spans
- [ ] Error output is readable by a human *and* machine-parseable
- [x] Asserts, with messages
- [ ] Failed runs are resumable after fixing the cause, where it makes sense
- [ ] Nothing panics: malformed input always yields a structured error
- [x] `raise`: user code raises its own error with a user-chosen `kind`, message and data payload
- [ ] Re-raise from a handler, preserving the original error as a `cause` chain
- [x] Match handlers on error `kind`, so an unexpected kind is not swallowed
- [ ] `finally` / cleanup that runs on both success and failure (needed before effects)
- [x] Errors are values in the saved state: a caught error stays readable in `root`, and the state diff shows it appear
- [ ] Validation can collect every problem in one run instead of stopping at the first

---

## 5. Reuse: programs bigger than one file

- [ ] Import another Helix file
- [ ] Namespacing and visibility rules
- [ ] A standard library with a documented, small, stable core
- [ ] Package or dependency story (even a minimal one: pinned path or git refs)
- [ ] Circular import detection with a useful error

---

## 6. Daily tooling

The difference between a language and a language I enjoy using.

- [ ] `helix run` with sensible exit codes (exists, extend as features land)
- [ ] REPL
- [ ] Formatter with canonical output
- [ ] `helix check`: parse and static checks without running
- [ ] Step/trace mode: watch evaluation move through the graph
- [ ] Inspect a state file: status, current position, pending effect, summary
- [ ] Watch mode: re-run on change
- [ ] Editor support: JSON Schema for programs, then an LSP
- [ ] Shell completion and a helpful `--help`
- [ ] In-language test runner (assertions plus golden state comparison)
- [ ] Single-command install (prebuilt binary or `cargo install`)

---

## 7. Trust

- [ ] Performance: a 10k-node program runs in well under a second
- [ ] Memory stays bounded on long runs
- [ ] Fuzzing of the parser and evaluator
- [ ] Sandbox/permission model for effects (files, network, commands)
- [ ] Stability promise: documented what may change and what is frozen
- [ ] Language reference (started: `LANGUAGE.md`) and a tutorial that a stranger could follow
- [ ] A changelog

---

## 8. Types: declarative, first-class, and the machine-config niche

The long-range target. A type is ordinary readable YAML, a value like any other, and a machine config is a type rather than a script: everything about it is known and checked at type time, before any effect touches a machine.

### Declarative types
- [ ] A type is declared as plain YAML data in the object graph (fields, their types, defaults), not in a separate schema language
- [ ] Scalar, list, map and record types, plus optional fields, unions, enums and literal types
- [ ] Refinements: a type can carry a predicate (`port` is an int in 1..65535), written as an ordinary Helix expression
- [ ] Annotate a value or a `fn` parameter/result with a type; a mismatch is a structured `type` error blaming the offending node with path and line
- [ ] Type checking collects every violation in one pass instead of stopping at the first
- [ ] Types survive in the saved state and round-trip: emitted `root` re-read yields the same types
- [ ] `helix check` type-checks a program without running it or performing effects

### First-class types
- [ ] Types are values: bind them with `let`, pass them to and return them from functions, store them in lists and maps
- [ ] Type constructors are ordinary functions from types (and values) to types (`[list_of, t]`, `[with_field, t, name, u]`)
- [ ] Reflection: read a type's fields, defaults and parents as data; ask `[is, value, t]` and `[subtype, a, b]`
- [ ] Type-time evaluation: a well-defined phase that runs pure Helix to compute types, with a guarantee it performs no effects and terminates or fails cleanly
- [ ] Type-time errors are the same structured errors as run-time ones, with the phase recorded in the state

### Type classes and inheritance
- [ ] Abstract type classes: declare required fields and constraints without a concrete value (`machine`, `server`, `has_ssh`)
- [ ] A type inherits from one or more classes; the result is the merge of their fields, defaults and constraints
- [ ] Documented, deterministic merge and override rules (which parent wins, what counts as a conflict, how lists and maps combine)
- [ ] Conflicts between parents are type-time errors that name both sources, never silent last-writer-wins
- [ ] An abstract class cannot be realized until every required field is filled; the error lists what is missing and which class demanded it
- [ ] Provenance: for any field of a resolved type, the state shows which class or override supplied it
- [ ] Parametric classes: a class takes arguments (`[web_server, {port: 8080}]`) and yields a class

### Machine configs as type-time types
- [ ] A machine config is a concrete type inheriting from abstract machine classes; it is fully resolved at type time with no machine contact
- [ ] The resolved config serializes to YAML that reads as the complete, flattened description of the machine and diffs well in `git diff`
- [ ] Same inputs, same resolved config: resolution is deterministic and pinned (imports, versions) so a config is reproducible
- [ ] Fleet-level checks at type time: constraints across several machine types (unique hostnames, every client's server exists, no port clashes)
- [ ] Realization is a separate, explicit step: compare a resolved config with a machine's observed state and produce a plan as data
- [ ] Applying a plan uses the effect system (section 3), is idempotent, and suspends/resumes per step (section 2) with the pending step visible in the state
- [ ] Drift report: what the machine has that its type does not say, and the reverse
- [ ] Secrets are referenced by a typed handle, never inlined into a resolved config or saved state

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

- `dogfood_config_diff`: added/removed/changed keys, nested value changes, null versus absence, unchanged and empty snapshots. Friction: building report records requires nested `set` calls because symbols in ordinary map values are literal data; nested changes are reported at the top-level key.

- `dogfood_project_inventory`: missing required fields, duplicate names and dangling dependencies, plus valid and empty inventories. Friction: presence checks do not validate field types; this example assumes string names and list dependencies. Schema validation remains separate work.

- `dogfood_dependency_order`: stable ordering of diamond dependencies, missing references, cycles, self-dependencies and empty graphs; independent work completes before blocked tasks are reported. Friction: immutable maps have no removal operation, so each batch rebuilds the pending map. Blocked results preserve the graph rather than claiming every blocked node belongs to a cycle.

- `dogfood_expense_reconciliation`: aggregate duplicate accounts and refunds in integer cents; report mismatched totals and accounts present on only one side, including zero balances; balanced and empty ledgers yield no findings. Friction: currency units and matching keys are conventions in user code, with no decimal-money type.

- `dogfood_work_queue`: a 100+ line stable priority-sort program with dispatch output, negative priorities, equal-priority payloads, sorted/reversed/empty/singleton inputs, independent calls, idempotence and source-order preservation. Friction: no comparator-based sort builtin; insertion sort repeatedly copies lists and is suitable only for small queues.

### Errors as a first-class feature (section 4)
- [ ] Parse and validate a messy inline dataset where every row may be malformed; `try` turns each failure into a record, and the report lists all of them with path and line
- [ ] A calculator or expression evaluator that raises its own `syntax`/`arity` errors and recovers per input line, never aborting the batch
- [ ] A retry loop: attempt a failing step, inspect the error's `kind`, retry on transient kinds, give up on the rest, and keep the attempt history in the state
- [ ] Wrap a low-level error in a domain error with `cause`, then read the full chain from the saved state without a debugger
- [ ] A function whose `assert` preconditions document its contract; call it wrongly and fix the caller from the assertion message alone
- [ ] Turn a failed run's saved error into a regression golden case in under a minute (error, expected state, fix)
- [ ] Cleanup with `finally` around a multi-step job, proven by a golden case where a middle step fails
- [ ] Diff two runs of one program, one passing and one failing, and explain the failure from the state diff alone

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

## Open questions

Decide these before the features that depend on them.

- [x] How are user-defined actors declared in YAML so they stay readable? `name: [fn, [params], body]`; called as `[name, args...]`
- [ ] What is the evaluation model for effects: pull, push, or explicit yield?
- [ ] Does a suspended state embed the source, reference it, or both?
- [ ] Types are coming (section 8), but how strict is ordinary scripting: must everything be annotated, or are types gradual and opt-in outside configs?
- [ ] Is inheritance nominal (declared parents) or structural (anything with the right fields satisfies a class), or both?
- [ ] What separates type time from run time in a language whose source is its state: a distinct phase, a status, or a marked region of the graph?
- [ ] How do parent classes merge: Nix-module-style priorities and merge functions, CUE-style unification, or linearized inheritance?
- [ ] What is the story for secrets in persisted state?
- [ ] Which parts of `flintwinters/helixcpp` are worth carrying over, and which are better left behind?
