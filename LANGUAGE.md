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

A list is an **expression** when its first element is an *unquoted* builtin name (the actor). Everything else is a plain list. To write a list that merely starts with a builtin's name, quote the head: `["add", 1, 2]` is the data `[add, 1, 2]`. Emitted state quotes such heads for you, so saved state round-trips.

Inside an expression, an unquoted string argument is a **symbol**. It resolves to the nearest enclosing map that defines that key, walking up parent links, so inner definitions shadow outer ones. A value that depends on itself is a `cycle` error.

Values are immutable: `set` and `append` return new values.

## Type rules

No implicit conversion, with one exception: **numeric promotion**. `int` op `int` is an `int`; if either operand is a `float` the result is a `float`. Everything else needs an explicit `str`, `int` or `float`.

## Builtins

**Arithmetic** (numbers): `add`, `mul` (2 or more args, left fold), `sub`, `div`, `mod` (exactly 2), `neg` (1). Integer `div` and `mod` truncate toward zero. Dividing by zero is `divide_by_zero`; integer or float overflow is `overflow`.

**Comparison**: `eq`, `ne` work on any values structurally (numbers compare by value, so `1` equals `1.0`). `lt`, `le`, `gt`, `ge` compare two numbers or two strings.

**Logic** (booleans only): `not`; `and`, `or` take 2 or more args, are *lazy* (stop at the first `false` / `true`, never evaluating the rest) and return a boolean. `[if, cond, then, else]` evaluates only the chosen branch.

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

**Conversion and inspection**: `[str, scalar]`, `[int, x]` (floats truncate toward zero; strings must parse), `[float, x]`, `[type, x]` (`integer`, `float`, `string`, `boolean`, `null`, `list`, `map`).

## Error kinds

`parse`, `unbound`, `type`, `arity`, `cycle`, `overflow`, `divide_by_zero`, `missing`, `range`, `conversion`, `unsupported`.
