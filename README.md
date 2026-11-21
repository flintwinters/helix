# Helix

**An experimental Rust runtime where programs, running state and errors are all
plain YAML you can read, diff and edit.**

## Why Helix

- **Execution is data.** Programs, results, paused machines and errors are YAML:
  read, diff, commit. Syntax is explicit (`!call`, `!ref`, `!literal`); strings are
  always literal; quoting and key order never change meaning.
- **Immutable, resumable machines.** Step = new machine; old versions keep their
  meaning. Versioned snapshots restore mid-execution (active calls, loops).
  Inspection YAML is read-only, not restorable.
- **Permissioned effects.** File, process, HTTP, clock, randomness: program
  requests, caller grants. Never source or saved state. Pending and completed effects
  visible in state; journals refuse to replay uncertain effects.
- **Declarative machine configuration.** Pure resolve and validate, reviewed plan,
  then authorized apply.
- **Designed with agents.** Model agents run recorded coding tasks against the CLI
  and language ([55 tasks](benchmarks/agent/README.md)). Transcripts and failures
  drive CLI discovery, syntax, library and error-message changes. Errors name what
  was rejected and the fix, and are catchable in-program.

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
