# Helix Charter Cockpit

**Goal:** Helix is a language I can reach for every day, for real scripts and automations, without apologizing for it.

The test for "daily driver" is simple: I start a task in Helix instead of Python or shell, I finish it, and I can open the saved state afterwards and understand exactly what happened.

How to use this file: tick a box only when the feature works end to end *and* has a golden case in `tests/cases/` (run `./manage.py test`). A feature that only works in a demo stays unticked. Edit freely; this file is for humans, while `AGENTS.md` holds the agent-facing architecture and invariants.

**Where we are:** walking skeleton only. YAML parses into an object graph, three integer builtins evaluate, and the result serializes back to YAML.

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
- [ ] Strings (literal, concatenation, length, slicing)
- [ ] Floats
- [ ] Booleans and null as first-class values in expressions
- [ ] Lists with index, length, append, concat
- [ ] Maps with get, set, keys, has
- [ ] Clear, documented type rules (no silent coercion)

### Operators
- [ ] Comparison: `eq`, `ne`, `lt`, `le`, `gt`, `ge`
- [ ] Boolean: `and`, `or`, `not`
- [ ] Arithmetic completion: `div`, `mod`, `neg`, float math
- [ ] Variadic forms where natural (`[add, 1, 2, 3]`)

### Control flow
- [ ] `if` / `cond` (lazy: only the chosen branch runs)
- [ ] Local bindings (`let`)
- [ ] Iteration: `map`, `filter`, `fold`, `each`
- [ ] A way to loop until a condition without unbounded recursion

### Functions
- [ ] User-defined actors (functions) declared in YAML
- [ ] Parameters and return values
- [ ] Closures over the enclosing object graph
- [ ] Recursion, with a stack-depth limit that fails cleanly
- [ ] Documented evaluation order (and what is lazy)

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

- [ ] `try` / recover form, with the error available as data
- [ ] Error values carry a call chain with paths and source spans
- [ ] Error output is readable by a human *and* machine-parseable
- [ ] Asserts, with messages
- [ ] Failed runs are resumable after fixing the cause, where it makes sense
- [ ] Nothing panics: malformed input always yields a structured error

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
- [ ] Language reference and a tutorial that a stranger could follow
- [ ] A changelog

---

## 8. Dogfooding milestones

Real programs, in order of ambition. Each one is a checkbox only when I actually use it.

- [ ] Replace one small shell script with Helix
- [ ] A config-transform pipeline (read YAML in, compute, write YAML out)
- [ ] A task runner for this repo (`./manage.py` logic expressed in Helix)
- [ ] A long-running workflow that suspends on an external event and resumes days later
- [ ] A program that inspects and edits another Helix program's saved state
- [ ] Helix running its own test suite
- [ ] A week where I reach for Helix first without thinking about it

---

## Open questions

Decide these before the features that depend on them.

- [ ] How are user-defined actors declared in YAML so they stay readable?
- [ ] What is the evaluation model for effects: pull, push, or explicit yield?
- [ ] Does a suspended state embed the source, reference it, or both?
- [ ] Strict types, gradual types, or none?
- [ ] What is the story for secrets in persisted state?
- [ ] Which parts of `flintwinters/helixcpp` are worth carrying over, and which are better left behind?
