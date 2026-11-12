# Helix Language Reference

A Helix program is a YAML document. Run it with `./manage.py run FILE` (or `helix run FILE`); the result is a state document:

```yaml
status: finished        # ready | running | finished | error | signaled
root: { ... }           # every value, evaluated
```

On failure `status: error` and an `error:` block replaces `root:` with `kind`, `message`, `path` (root-relative, e.g. `/inner/y/1`), `line` and `column` (both 1-indexed).

## Values

| Kind    | Source examples            | Notes |
|---------|----------------------------|-------|
| integer | `42`, `-7`                 | 64-bit, overflow is an error |
| float   | `3.5`, `1e3`, `2.0`        | finite only; `inf`/`nan` never appear |
| string  | `hello`, `"hello, world"`  | unquoted strings are *symbols* inside expressions; quoted strings are always literal |
| boolean | `true`, `false`            | |
| null    | `null`, `~`                | |
| list    | `[1, 2, 3]`, block `- x`   | elements are evaluated |
| map     | `{a: 1}`, block `k: v`     | string keys, insertion order kept; values are evaluated |

## Expressions

A list is an **expression** when its first element is an *unquoted* builtin name or the name of a user function (the actor). Everything else is a plain list. To write a list that merely starts with a builtin's name, quote the head: `["add", 1, 2]` is the data `[add, 1, 2]`. Emitted state quotes every string at the head of a data list for you (`- "a"`), so saved state round-trips whatever functions are in scope.

Inside an expression, an unquoted string argument is a **symbol**. It resolves to the nearest enclosing map that defines that key, walking up parent links, so inner definitions shadow outer ones. Function parameters and `let` bindings are in scope inside their bodies (see below). A value that depends on itself is a `cycle` error.

Values are immutable: `set` and `append` return new values.

## Functions and local names

`[fn, [params], body]` declares a function; give it a name by putting it in a map: `double: [fn, [n], [mul, n, 2]]`. A list whose head resolves to such a function is a call: `[double, 4]`. Arguments are evaluated first (left to right) and bound to the parameters; a wrong argument count is an `arity` error blamed on the call. A function evaluates to its own source, so it appears unchanged in saved state.

Scope is **lexical**, through parent links: a body sees its parameters, then the maps enclosing the *definition* (not the caller), so a function closes over the object graph it is written in. Recursion works through the function's own name. Calls nested deeper than 200 fail with a `depth` error blamed on the call. Functions are not values: one may only head a call or be passed, by name or as an inline `fn`, where a form expects a function; anywhere else it is a `type` error.

`[try, expr, handler]` evaluates `expr`; if that fails, `handler` (a function name or inline `fn`) is called with the error as a map (`kind`, `message`, `path`, `line`, `column`, as in saved error state) and its result replaces the failure. Without a failure the handler never runs. `[assert, cond]` / `[assert, cond, "message"]` returns `null` when `cond` is `true`, otherwise fails with an `assertion` error carrying the message. `[raise, kind, message]` / `[raise, kind, message, data]` fails with a user-chosen `kind` string (a non-empty string), message and optional plain-value `data` payload, blamed on the `raise` expression; it has the same shape as a builtin error and `data` appears in the error map. `[raise, error]` re-raises a caught error map (so a handler can match the kinds it expects with `cond` and re-raise the rest rather than swallow them).

`[let, {name: expr, ...}, body]` evaluates `body` with the bindings in scope. Bindings see each other (mutually, as in a map) and are evaluated only when used; a self-dependent binding is a `cycle`.

**Iteration** takes a function (a name or inline `fn`) first, like every actor: `[map, f, list]`, `[filter, f, list]` (`f` must return a boolean), `[fold, f, init, list]` (`f` takes accumulator then element), `[each, f, list]` (calls `f` for each element, returns `null`; useful once effects exist). `[until, done, step, init]` repeats `state = step(state)` until `done(state)` is `true` and returns the state; more than 100000 steps is a `limit` error.

**Evaluation order**: arguments left to right, all of them, except the forms that choose: `if`, `cond`, `and`, `or` (lazy), `fn` (body not run until called), `let` (bindings on use). Memoization is per node outside function bodies; inside a body every call re-evaluates.

## Type rules

No implicit conversion, with one exception: **numeric promotion**. `int` op `int` is an `int`; if either operand is a `float` the result is a `float`. Everything else needs an explicit `str`, `int` or `float`.

## Builtins

**Arithmetic** (numbers): `add`, `mul` (2 or more args, left fold), `sub`, `div`, `mod` (exactly 2), `neg` (1). Integer `div` and `mod` truncate toward zero. Dividing by zero is `divide_by_zero`; integer or float overflow is `overflow`.

**Comparison**: `eq`, `ne` work on any values structurally (numbers compare by value, so `1` equals `1.0`). `lt`, `le`, `gt`, `ge` compare two numbers or two strings.

**Logic** (booleans only): `not`; `and`, `or` take 2 or more args, are *lazy* (stop at the first `false` / `true`, never evaluating the rest) and return a boolean. `[if, cond, then, else]` evaluates only the chosen branch. `[cond, c1, v1, c2, v2, ..., default]` tests the conditions in order and evaluates only the value of the first `true` one, else the default (a missing default is an `arity` error).

**Collections** (strings index by character):

| Form | Result |
|------|--------|
| `[len, x]` | length of a string, list or map |
| `[concat, a, b, ...]` | all strings, or all lists |
| `[slice, x, start, end]` | half-open range of a string or list |
| `[get, x, key]` / `[get, x, key, default]` | list index or map key; `missing` error without a default |
| `[set, x, key, value]` | replaces an existing list index, or inserts/replaces a map key |
| `[append, list, v, ...]` | list with values added at the end |
| `[keys, map]` | list of keys |
| `[has, x, y]` | map: key present; list: element present (by `eq`); string: substring present |

**Conversion and inspection**: `[str, scalar]`, `[int, x]` (floats truncate toward zero; strings must parse), `[float, x]`, `[type, x]` (`integer`, `float`, `string`, `boolean`, `null`, `list`, `map`). A type is a value: the string `type` returns. `[is, x, t]` tests `x` against type name `t` (or `any`); an unknown name is a `type` error.

## Libraries and imports

`[import, "helpers.yaml"]` loads a Helix document. Relative paths resolve from the importing file's directory; stdin resolves from the working directory. All imports are linked at load time, including imports inside functions and unchosen branches. The specifier is a literal string, quoted or plain, never an evaluated expression. Missing files, unknown standard modules and circular dependencies fail before execution, outside `try` handlers.

A library normally exports a map. Every key is public; its definitions resolve lexically inside the library and cannot see the importing program's names. Files are identified by canonical path and linked once per program. Imports can themselves import other libraries. There is no package registry, version resolver or implicit search path.

```yaml
math: [import, std:math]
helpers: [import, "helpers.yaml"]
answer: [math.square, 6]
```

Dotted symbols select map members (`helpers.constant`, `helpers.nested.function`); an exact lexical binding containing dots takes precedence. Use imported functions as call heads or iteration callbacks, such as `[map, math.square, [1, 2, 3]]`. Missing members in argument position are `unbound` errors; an unresolved call head remains a data list, following ordinary expression detection. Module errors include `file`; their `path`, `line`, and `column` refer to that file.

Imports evaluate to their document's value. Finished output embeds the evaluated library map, retaining function source, so ordinary imported functions can still be resolved on re-reading. Function source containing relative imports still requires those files; output is not a standalone package or a closure snapshot.

Standard libraries are Helix source embedded in the executable:

| Import | Exports |
|--------|---------|
| `std:math` | `abs(n)`, `sign(n)`, `min(a,b)`, `max(a,b)`, `clamp(n,lo,hi)`, `square(n)` |
| `std:list` | `sum(xs)`, `product(xs)`, `reverse(xs)`, `range(n)` (integers from zero while less than `n`, bounded by the iteration limit) |

`./manage.py std` lists modules; `./manage.py std math` prints a module's source. The binary also accepts `helix std [module]`.

## Error kinds

`import` (module loading or circular dependency), `parse`, `unbound`, `type`, `arity`, `cycle`, `overflow`, `divide_by_zero`, `missing`, `range`, `conversion`, `unsupported`, `depth` (call nesting), `limit` (iteration cap), `assertion` (failed `assert`), plus any kind chosen by user code with `raise`.
