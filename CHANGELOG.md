# Changelog

Major capability changes in the experimental 0.1 development line. These are
milestones, not stable releases; compatibility boundaries live in
[LANGUAGE.md](LANGUAGE.md). Detailed implementation history remains in Git.

## Current development

- Versioned executable snapshots preserve complete bounded continuations, captures,
  linked source graphs and pending effects across processes. Explicit snapshot and
  restore commands support live-local edits and reject unsupported versions;
  inspection and pure source recovery keep their existing separate formats.

- Declarative types now validate nested defaults before membership and report
  expected values, actual values, missing-field presence and predicate failures
  with source blame. Direct function arguments retain their own locations.
  Successful annotations leave bounded inspection evidence; live captures are
  summarized and truncation is explicit.
- Non-executing checks cover a bounded annotation and expression fragment,
  including unused function bodies and ordinary type descriptors. Unsupported
  reasoning stays explicitly unchecked. Runtime contracts remain authoritative.
- Caller-granted SSH commands add strict host-key verification, noninteractive
  authentication and explicit escalation grants. Loopback acceptance covers the
  documented subset; general remote provisioning and durable recovery remain open.

## Configuration and development workflows

- Typed inventory, resource providers, guarded commands and dependency/handler
  orchestration support explicit observation, review, application and verification.
  A disposable Docker nginx workload demonstrates local convergence, no-op
  reapplication, drift repair and fresh reconciliation after failures.
- Immutable linked file modules are usable in pure type-time regions. Purity and
  source-loading authority remain separate; reproducibility depends on the whole
  linked source graph and recorded inputs.
- Production testing uses Helix policy for selection, reports, golden decisions
  and robustness mutation plans. Python retains independent bootstrap, process
  supervision and validation of policy claims. SQLite effects support usage logging.
- The redundant `closure` form was retired in favor of ordinary `let` and `fn`.
  Core semantics, library APIs and host operations now have separate manuals.

## Runtime foundation

- Explicit YAML calls, lexical references and literals define a pure expression
  language with immutable collections, first-class closures and retained machines.
  Structured errors retain source locations, call chains and causes.
- Type descriptors, refinements, annotations, class composition and pure type-time
  evaluation support configuration planning as ordinary values and functions.
- Caller-authorized effects provide file/stream access, local commands, HTTP,
  environment, time and randomness. Pending effects and interruption preserve
  in-memory continuations; saved inspection graphs are not executable snapshots.
- Imports, pure error recovery, stepping, inspection, REPL, formatting, watch,
  completion and LSP support daily development through `manage.py`.
