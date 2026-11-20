# Changelog

Major capability changes in the experimental 0.1 development line. These are
milestones, not stable releases; compatibility boundaries live in
[LANGUAGE.md](LANGUAGE.md). Detailed implementation history remains in Git.

## Current development

- Errors carry an optional advisory `hint` derived from the builtin registry,
  lexical scope, standard-module exports and reached map members: builtins used as
  values, unimported std exports, misspelled names and members, module maps used as
  call actors and unsupported tags such as `!if`. Messages name the rejected tag or
  actor. Executable snapshots move to version 3; versions 1–2 migrate with no hint.

- `std:linux` packages form an installer layer keyed by manager: configuration nests
  `packages: {apt: {...}}` and ids are `package:MANAGER:NAME`. `apt` is the only
  defined and Docker-qualified manager; others need their own workload and fixture.

- Caller-owned durable effect journals preserve completed outcomes, refuse blind
  replay after controller death, and support explicit reconciliation of the exact
  uncertain request. Restoration requires fresh grants; target locking, rollback
  and secret-safe persistence remain separate obligations.

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
  documented subset; general remote provisioning remains open. Durable journals
  supply a separate effect-boundary recovery contract.

## Configuration and development workflows

- Typed inventory, resource providers, guarded commands and dependency/handler
  orchestration support explicit observation, review, application and verification.
  A disposable Docker nginx workload demonstrates local convergence, no-op
  reapplication, drift repair and fresh reconciliation after failures. The same
  policy is qualified over caller-granted SSH, including explicit uncertainty
  reconciliation and fresh-process continuation after controller death. This
  covers one pinned Debian container/root/helper combination, not a general
  remote platform or a completed Ansible migration.
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
- Imports, pure error recovery, stepping, inspection, REPL, formatting, watch
  and LSP support daily development through `manage.py`.
