# Helix

Helix is an experimental language and runtime written in Rust. Programs are YAML;
results and errors are YAML too. The idea is to make execution state as easy to
read, diff, and edit as source code—and eventually to resume a program from that
state.

Today machines can run, pause between explicit transitions, and branch immutably in memory. Disk save/restore and the reversibility promise are not implemented.

## Try it

You need Rust with Cargo (2024 edition support) and Python 3.10+.

```sh
./manage.py run tests/cases/arithmetic.yaml
```

This builds the runtime and runs:

```yaml
x: 2
y: [add, x, 1]
z: [mul, [sub, y, 1], 5]
```

An expression starts with an actor: `[add, x, 1]` adds one to `x`. The result is:

```yaml
status: finished
root:
  x: 2
  "y": 3
  z: 10
```

The emitter quotes strings where YAML could otherwise change their meaning.
An ordinary-data `root` can be run again and produces the same values. Functions and machines use named object inspection graphs; these are not executable snapshots.

For a result without the state envelope:

```sh
./manage.py runtime exec '[add, 20, 22]'
# 42
```

After building, use `target/debug/helix` directly, or install it with
`cargo install --path .`. `helix exec` accepts inline source or reads stdin;
string results are written verbatim, other values as YAML, and errors go to stderr.
Run `helix --help` for the other commands.

For [jq](https://jqlang.org/manual/) and [yq](https://mikefarah.gitbook.io/yq/commands/evaluate)
pipelines, select an explicit output format so string results remain quoted data:

```sh
helix exec --json '{answer: [add, 20, 22]}' | jq '.answer'
helix exec --yaml '{answer: [add, 20, 22]}' | yq '.answer'
```

Both flags work with `./manage.py runtime exec` and stdin. JSON input is accepted
as YAML source; expression-shaped arrays still follow Helix evaluation rules.
Use `set -o pipefail` to detect failures anywhere in a shell pipeline.
The `pipeline` suite checks encodings and actual consumers when installed;
CI requires `jq` and `yq` via `HELIX_REQUIRE_PIPELINE_TOOLS=1`.

## A little more language

Helix has immutable values, lexical scope, functions, recursion, collections,
imports, first-class types, and structured errors with source locations.
Inside expressions, unquoted strings refer to names; quoted strings are literals.

This example is in [tests/cases/tutorial.yaml](tests/cases/tutorial.yaml):

```yaml
math: [import, std:math]
prices: [list, 3, 5, 8]
square_price: [fn, [price], [math.square, price]]
squared: [map, square_price, prices]
valid: [assert, [eq, squared, [list, 9, 25, 64]], "unexpected squared prices"]
recovered: [try, [div, 1, 0], [fn, [error], [get, error, "kind"]]]
```

Run it with `./manage.py run tests/cases/tutorial.yaml`. `squared` becomes
`[9, 25, 64]`, the passing assertion returns null, and `try` captures the division
error as `divide_by_zero`. Change a price without updating the assertion to see
a failure and its source location.

See the [language reference](LANGUAGE.md) for syntax, builtins, types, imports,
and recovery.

## Immutable execution

Rust callers use `Vm::step(&self) -> Vm`, `run(&mut self)` and `inspect()`.
Helix programs create ready children with `[vm, function]`, advance versions
with `[step, child]`, and read state with `[inspect, child]`. Earlier versions
keep their meaning when another branch advances. See
[`vm_parent_child.yaml`](tests/cases/vm_parent_child.yaml) for a complete example.
Functions retain direct code and lexical environments. Calls, iterations,
refinements, pure phases, resource budgets and cleanup are explicit machine state.
Function/machine pipeline results retain both their value projection and object table.

## Status

The longer-term aim is declarative machine configuration: describe and validate
a machine with types, then apply changes in a separate step. The pure type/class
algebra exists; applying machine changes does not. The [charter](CHARTER.md)
tracks that work and the suspend/resume design.

The language, Rust API, and saved-state format are experimental and may change.
Saved states have no format version or migration support yet. Keep source and
the runtime revision together when reproducibility matters. Recovery can retry
unfinished fields after an error, but cannot restore active calls or iterations.

## Development

`./manage.py` is the project tooling entrypoint. Run it without arguments to
browse commands, or append `--help` at any level.

```sh
python3 -m pip install PyYAML                # needed for tests and inventory
./manage.py test --describe                 # available suites and what they check
./manage.py test --suite golden --suite cli # a focused test run
./manage.py test                            # all suites
./manage.py lint --format=text
```

Lint uses the [hyperlinter](https://github.com/flintwinters/hyperlinter) submodule
and needs Git, Node.js, and npm; the command sets it up when needed.

- [Testing guide](tests/README.md): coverage, gaps, writing tests, and benchmarks.
- [AGENTS.md](AGENTS.md): architecture, invariants, and change discipline.
- [std/](std/): bundled libraries written in Helix; `./manage.py std` lists them.

The earlier C++ experiment lives in
[flintwinters/helixcpp](https://github.com/flintwinters/helixcpp).
