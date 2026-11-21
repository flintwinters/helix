# Helix

**An experimental Rust runtime where programs, running state and errors are all
plain YAML you can read, diff and edit.**

## Why Helix

- **Execution is as readable as source.** Programs are YAML. Results, paused
  machines and errors come back as YAML too, so a run can be inspected, diffed
  and committed like any other file.
- **Immutable machines.** Step a machine and you get a new one; the old version
  keeps its meaning. Branch, compare or retry from any earlier state without
  copying or locking anything.
- **Resumable.** Versioned snapshots restore a program mid-execution, including
  active calls and loops. Inspection output is for reading, not for restoring.
- **Errors that teach.** Every failure carries a structured kind, a source
  location, what was rejected and the concrete fix. Programs can catch and
  branch on them.
- **No hidden behavior.** Strings are always literal. Calls and references are
  explicit (`!call`, `!ref`, `!literal`). Quoting style and key order never
  change meaning.
- **Effects need permission.** File, process, HTTP, clock and randomness access
  is requested by the program and granted by the caller, never by the source or
  by saved state. Pending and completed effects are visible in the machine state,
  and journals refuse to replay an uncertain effect.
- **Built for declarative machine configuration.** Resolve and validate purely
  with types, review the plan, then apply explicitly authorized changes.
- **Designed for agents.** Discoverable CLI and docs, scoped output, and complete
  JSON reports on request.

```yaml
math: !call [import, 'std:math']
prices: !call [list, 3, 5, 8]
square_price: !call [fn, [price], !call [math.square, !ref price]]
squared: !call [map, !ref square_price, !ref prices]
recovered: !call [try, !call [div, 1, 0], !call [fn, [error], !call [get, !ref error, kind]]]
```

`squared` is `[9, 25, 64]`; `recovered` is `divide_by_zero`.
([Full example](tests/cases/tutorial.yaml))

## Status

Experimental. The language, Rust API and snapshot format may change. The only
real-world workload so far is a scoped Docker nginx deployment; general fleet and
machine management is future work.

## Documentation

- [Language](LANGUAGE.md): syntax, evaluation, types, imports, recovery
- [Standard libraries](LIBRARY.md)
- [Host operations](HOST.md): effects, grants, command and SSH lifecycle
- [Charter](CHARTER.md): design principles and roadmap
- [Testing](tests/README.md) and [AGENTS.md](AGENTS.md): working on Helix
