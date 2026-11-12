# Helix

Helix is an experimental language and runtime written in Rust. Programs are YAML;
results and errors are YAML too. The idea is to make execution state as easy to
read, diff, and edit as source code—and eventually to resume a program from that
state.

Today it runs to completion or error. Full suspend/resume is not implemented.

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
The finished `root` can be run again and produces the same values.

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

### Change workflow

- Keep implementation and regression coverage in the same logical checkpoint.
  Extend existing suites through `./manage.py`; keep generated fixtures and
  artifacts in `runtime/`, never `/tmp`. Parallelize independent tests whenever
  possible; serialize only where shared state requires it.
- Put pure library laws in dogfood modules, exact output contracts in golden
  cases, and host/process contracts in Python suites. The [testing guide](tests/README.md)
  maps coverage and explains suite selection.
- Keep lint at zero diagnostics without weakening policy or adding suppression
  baselines. Prefer private or `pub(crate)` implementation and a small public
  API; reuse shared leaf modules without child-to-parent dependency cycles.
- Stage explicit paths only, never `git add .` or `git add -A`. Commit every
  verified logical checkpoint with a detailed message.

Use `./manage.py --help` or `--help-json` for command discovery,
`./manage.py test --describe` for suite contracts, and `./manage.py std [module]`
to discover embedded libraries and read their source.
