# Helix

Helix is a small experimental runtime written in Rust. It reads YAML into an object graph, evaluates expressions over maps, lists, strings, numbers, and functions, and writes the resulting machine state back to YAML. This continues the experiment started in the [original C++ implementation](https://github.com/flintwinters/helixcpp).

The central idea is that program source and machine state should share one readable representation. You should be able to inspect a run, diff it, edit it, and eventually resume it as plainly as you edit source. Syntax, output, and tooling aim to be discoverable for both people and agents.

## Try it

You need Rust with Cargo (supporting the 2024 edition), Python 3.10 or newer, and [PyYAML](https://pypi.org/project/PyYAML/). The Python entrypoint imports PyYAML for all commands.

```sh
python3 -m pip install PyYAML
./manage.py build
./manage.py run tests/cases/arithmetic.yaml
```

That example is a complete program:

```yaml
x: 2
y: [add, x, 1]
z: [mul, [sub, y, 1], 5]
```

Lists with an actor first are expressions. Here, `y` refers to `x`, and `z` refers to `y`. Running it prints:

```yaml
status: finished
root:
  x: 2
  "y": 3
  z: 10
```

The emitter quotes strings where needed to preserve their meaning when read again. The evaluated `root` can itself be run as a program; the test suite checks that finished roots remain stable across this round trip.

Use `./manage.py run -` to read a program from stdin. After building, the executable is also available as `target/debug/helix run FILE`.

## What works today

The runtime supports lexical name resolution, arithmetic and collections, lazy conditionals, local bindings, user functions and recursion, iteration, file imports, embedded standard libraries, and structured errors with source locations. Unquoted strings inside expressions resolve as names; quoted strings are literals. Values are immutable.

```yaml
math: [import, std:math]
answer: [math.square, 6]
double: [fn, [n], [mul, n, 2]]
results: [map, double, [1, 2, 3]]
```

Execution currently runs to completion or error. Suspend/resume remains a design goal: a machine promising reversibility must expose all state needed to restore its execution, including calls, locals, continuations, and pending effects. That promise is not implemented yet.

The longer-term direction is declarative machine configuration through first-class types: describe and validate a machine before applying changes to it. See the [charter](CHARTER.md) for the roadmap and the [language reference](LANGUAGE.md) for documented syntax and semantics.

## Development

`./manage.py` is the single entrypoint for project tooling:

```sh
./manage.py build             # compile the Rust crate
./manage.py run FILE          # evaluate and emit YAML state
./manage.py std               # list embedded standard modules
./manage.py std math          # print a module's Helix source
./manage.py test              # golden cases, round trips, and malformed-input checks
./manage.py test --bless      # deliberately regenerate golden expectations
./manage.py lint --format=text
```

Linting uses the [hyperlinter](https://github.com/flintwinters/hyperlinter) submodule and requires Git, Node.js, and npm. The lint command initializes the submodule and installs its dependencies when needed.

The Rust crate exposes a library and a CLI. The source is organized around:

| Path | Responsibility |
| --- | --- |
| `src/graph.rs` | YAML parsing, object graph, parent links, and source spans |
| `src/eval.rs` | Evaluation, lexical scope, calls, and control flow |
| `src/builtins.rs`, `src/builtins/` | Builtin operations and shared value/error helpers |
| `src/vm.rs` | VM lifecycle and serialized state |
| `src/link.rs`, `src/library.rs`, `std/` | File imports and embedded Helix libraries |
| `src/emit.rs`, `src/error.rs` | Round-trippable YAML output and structured diagnostics |
| `tests/` | Programs paired with expected YAML states |

Read [AGENTS.md](AGENTS.md) for architectural invariants and change discipline.
