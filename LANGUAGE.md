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

Scope is **lexical**, through parent links: a body sees its parameters, then the maps enclosing the *definition* (not the caller), so a function closes over the object graph it is written in. Recursion works through the function's own name. Calls nested deeper than 200 fail with a `depth` error blamed on the call. Functions are values: pass or return them, bind them with `let`, and store them in lists and maps. `[call, function_expression, arg, ...]` calls any computed function; a named binding holding a computed function can also head a call. Iteration and `try` accept computed functions too. `[type, f]` reports `function`, and `function` is a primitive type usable in annotations. A quoted data list beginning with `"fn"` is still a list.

When a function travels as a value, its free lexical bindings are captured. Captures serialize as `[closure, {name: value, ...}, [fn, [params], body]]`; the binding map is the function's lexical scope on reload. Only referenced bindings are captured; capturing evaluates those bindings at transfer time. Different constructor calls retain independent environments. Ordinary function declarations retain their source form. Functions and captured bindings compare structurally by source and values; this does not prove that two functions compute the same result. Serialized closure-body errors currently refer to the reconstituted source.

`[try, expr, handler]` evaluates `expr`; if that fails, `handler` (a function name or inline `fn`) is called with the error as a map (`kind`, `message`, `path`, `line`, `column`, as in saved error state) and its result replaces the failure. Without a failure the handler never runs. `[assert, cond]` / `[assert, cond, "message"]` returns `null` when `cond` is `true`, otherwise fails with an `assertion` error carrying the message. `[raise, kind, message]` / `[raise, kind, message, data]` fails with a user-chosen `kind` string (a non-empty string), message and optional plain-value `data` payload, blamed on the `raise` expression; it has the same shape as a builtin error and `data` appears in the error map. `[raise, error]` re-raises a caught error map (so a handler can match the kinds it expects with `cond` and re-raise the rest rather than swallow them).

`[let, {name: expr, ...}, body]` evaluates `body` with the bindings in scope. Bindings see each other (mutually, as in a map) and are evaluated only when used; a self-dependent binding is a `cycle`.

**Iteration** takes a function (a name or inline `fn`) first, like every actor: `[map, f, list]`, `[filter, f, list]` (`f` must return a boolean), `[fold, f, init, list]` (`f` takes accumulator then element), `[each, f, list]` (calls `f` for each element, returns `null`; useful once effects exist). `[until, done, step, init]` repeats `state = step(state)` until `done(state)` is `true` and returns the state; more than 100000 steps is a `limit` error.

**Evaluation order**: arguments left to right, all of them, except the forms that choose: `if`, `cond`, `and`, `or` (lazy), `fn` (body not run until called), `let` (bindings on use). Memoization is per node outside function bodies; inside a body every call re-evaluates.

`[finally, expr, cleanup]` evaluates `expr`, then evaluates `cleanup` whether `expr` succeeds or fails. A successful cleanup discards its own value and preserves the original result or error. A failing cleanup replaces the original result or error. Cleanup is an expression, not a callback; use a function call when needed. This applies to evaluation errors; parse and import failures happen before evaluation.

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

**Conversion and inspection**: `[str, scalar]`, `[int, x]` (floats truncate toward zero; strings must parse), `[float, x]`, `[type, x]` (`integer`, `float`, `string`, `boolean`, `null`, `list`, `map`, `function`). A type is a value: a primitive name or a declaration map. `[is, x, t]` tests `x` against any declared type (or `any`); an unknown name is a `type` error.

## First-class types

`any` is the top type: every value inhabits it. `never` is the bottom type: no value inhabits it, including `null`, and it is a subtype of every type. `null` itself remains inhabited by the null value. A `list_of never` accepts only the empty list; a `map_of never` accepts only the empty map. Adding `never` to a union does not change its accepted values. Subtyping remains conservative: it does not discover every empty type or normalize declarations.

Types use ordinary value evaluation: `let`, function arguments and results, collections, conditionals and iteration all work with primitive names, records, unions, enums, literals and refinements. Constructors validate computed type arguments and blame the offending argument when it is not a type.

```yaml
list_type: [fn, [t], [list_of, t]]
strings: [list_type, string]
at_least: [fn, [minimum], [refine, integer, [fn, [n], [ge, n, minimum]]]]
positive: [at_least, 1]
valid: [is, 5, positive]
```

The pure type constructors are `list_of`, `map_of`, `record` (alternating field names and specs), `optional`, `default`, `union`, `enum`, `literal` and `refine`. Records are closed. `fields` returns a record or class's field specs as a map, including optional/default wrappers; `with_field` adds or replaces a record field. The `std:types` module derives records using ordinary functions: `from_fields(specs)` reconstructs a record from reflection, `without_field(t, name)` drops a field (an absent name is unchanged), and `optional_fields(t)` makes required fields optional while retaining existing optional fields and defaults. These functions preserve field order and return fresh records; projecting a class's fields does not copy its parents, fixed values or provenance. They fold over the existing checked constructors, with no separate type evaluator. `[subtype, a, b]` checks the existing structural type algebra conservatively: arbitrary predicates are not proved equivalent. `[violations, value, t]` returns every nested violation; `[expect, value, t]` returns the value or raises a structured type error containing those violations.

Subtyping uses identity and `any` as the top type. Lists and maps are covariant in their element type. A union is below a type when every alternative is below it; a type is below a union when one alternative covers it. An enum or literal is below a type when every listed value inhabits it. A refinement is below its base; proving a value belongs to a refinement still runs its predicate. Closed records admit no fields outside the target record, each source field must have a subtype of the corresponding target field, and every required target field must be required in the source. Defaults do not change membership. Unproved relationships return `false`; no theorem prover runs for arbitrary predicates.

`eq` on types is structural equality of their declaration values: record field order is ignored, list order (including union members and enum alternatives) is significant, and defaults and captured predicates participate. This is declaration equality, not equality of the sets of accepted values. Types retain their readable declaration maps in saved state and round-trip through the same evaluator.

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
| `std:types` | `from_fields(specs)`, `without_field(t,name)`, `optional_field(spec)`, `optional_fields(t)` |
| `std:math` | `number` (numeric type), `abs(n)`, `sign(n)`, `min(a,b)`, `max(a,b)`, `clamp(n,lo,hi)`, `square(n)` |
| `std:list` | `numbers` (numeric list type), `sum(xs)`, `product(xs)`, `reverse(xs)`, `range(n)` (integers from zero while less than `n`, bounded by the iteration limit), `unique(xs)` (first occurrence, structural/numeric equality), `sort_by(xs, before)` (stable; strict ordering predicate), plus [access, traversal and grouping helpers](#list-helpers) |

Standard modules dogfood ordinary function annotations. `std:math` exports `number` (`integer | float`); all its helpers take and return numbers, with `sign` returning an integer. `std:list` reuses that type for its `numbers` list type and for `sum`/`product`, which validate every element before folding. `reverse`, `unique` and `sort_by` require lists and return lists; `sort_by` requires a function even for an empty input. Its comparator must return a boolean and define a strict ordering; the annotation checks that it is a function, not that ordering law. `range` takes an integer and returns a list of integers; negative inputs still return an empty list. Contract failures are structured type errors at the call site with argument names and nested violation paths.

### List helpers

Import `l: [import, std:list]`. Helpers take the list first; predicates and transforms are ordinary functions. All helpers are pure and preserve input order unless sorting explicitly changes it.

| Helpers | Contract |
|---------|----------|
| `first(xs, fallback)`, `last(xs, fallback)` | Return the fallback only for an empty list; null and false remain ordinary elements. |
| `take(xs, n)`, `drop(xs, n)` | Take or skip up to `n` elements; clamp to length. A negative integer is a `range` error. |
| `enumerate(xs)` | Produce `[index, value]` pairs starting at zero. |
| `zip(xs, ys)` | Produce `[left, right]` pairs, stopping at the shorter list. |
| `flatten(xss)`, `flat_map(xs, transform)` | Flatten exactly one level; every inner list or transform result must be a list. |
| `find_index(xs, predicate)` | First matching index, or `-1`. |
| `find(xs, predicate, fallback)` | First matching element, or the explicit fallback. |
| `any(xs, predicate)`, `all(xs, predicate)` | Short-circuit left to right; empty inputs yield false and true respectively. |
| `count(xs, predicate)` | Count matches. |
| `partition(xs, predicate)` | Return `[matching, nonmatching]`, preserving order and calling the predicate once per element. |
| `group_by(xs, key_for)` | Group into a map by string keys, using `std:group.by`; key order follows first occurrence and buckets retain input order. |
| `frequencies(xs)` | Count strings, including empty strings, in first-occurrence key order. |
| `sort(xs)` | Ascending numeric or string order, using `sort_by`. Mixed incomparable values are type errors when compared. |

Callbacks are checked as functions even for empty lists. Predicates must return booleans when called. Searches stop after the first decisive element; fallback arguments are evaluated eagerly like other function arguments. Immutable list/map copies and repeated scans can be quadratic, including sorting, grouping and deduplication. These helpers target small inputs and obey the runtime iteration limits; none recurses in proportion to input length.

`./manage.py std` lists modules; `./manage.py std math` prints a module's source. The binary also accepts `helix std [module]`.

`std:group` exports `by(xs, key)`: group a list into a map using a function returning a string key. Empty and Unicode keys are valid. Keys follow first occurrence order, and each group's items retain input order. The key function runs once per item; callback failures propagate. List and function arguments are checked even for empty input. Immutable map/list copying can take quadratic time and total allocations; use small inputs within the runtime iteration limit.

## Error kinds

`import` (module loading or circular dependency), `parse`, `unbound`, `type`, `arity`, `cycle`, `overflow`, `divide_by_zero`, `missing`, `range`, `conversion`, `unsupported`, `depth` (call nesting), `limit` (iteration cap), `assertion` (failed `assert`), plus any kind chosen by user code with `raise`.
