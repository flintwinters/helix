# Helix Language Reference

A Helix program is a YAML document. Run it with `./manage.py run FILE` (or `helix run FILE`); the result is a state document:

```yaml
status: finished        # ready | running | finished | error | signaled
root: { ... }           # every value, evaluated
```

On failure `status: error` and an `error:` block replaces `root:` with `kind`, `message`, `path` (root-relative, e.g. `/inner/y/1`), `line` and `column` (both 1-indexed). Errors within source also carry an unambiguous `reference`: a list of string keys and integer list indices. The root is `[]`; an empty key is `[""]`, and a slash in a key stays inside that string.

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
| function | `[fn, [x], [add, x, 1]]` | direct code and lexical environment; distinct from list data |
| vm | `[vm, [fn, [], 42]]` | immutable execution state; created ready without running its body |

## Expressions

A list is an **expression** when its first element is an *unquoted* builtin name or the name of a user function (the actor). Everything else is a plain list. To write a list that merely starts with a builtin's name, quote the head: `["add", 1, 2]` is the data `[add, 1, 2]`. Emitted state quotes every string at the head of a data list for you (`- "a"`), so ordinary data round-trips whatever functions are in scope. Function and machine inspection graphs are not executable serialization.

Inside an expression, an unquoted string argument is a **symbol**. It resolves to the nearest enclosing map that defines that key, walking up parent links, so inner definitions shadow outer ones. Function parameters and `let` bindings are in scope inside their bodies (see below). A value that depends on itself is a `cycle` error.

Values are immutable: `set` and `append` return new values.

## Functions and local names

`[fn, [params], body]` declares a function; give it a name by putting it in a map: `double: [fn, [n], [mul, n, 2]]`. A list whose head resolves to such a function is a call: `[double, 4]`. Arguments are evaluated first (left to right) and bound to the parameters; a wrong argument count is an `arity` error blamed on the call. A function evaluates to a dedicated function value; inspecting or printing it does not turn its code into a data list.

Scope is **lexical**, through parent links: a body sees its parameters, then the maps enclosing the *definition* (not the caller), so a function closes over the object graph it is written in. Recursion works through the function's own name. Calls nested deeper than 200 fail with a `depth` error blamed on the call. Functions are values: pass or return them, bind them with `let`, and store them in lists and maps. `[call, function_expression, arg, ...]` calls any computed function; a named binding holding a computed function can also head a call. Iteration and `try` accept computed functions too. `[type, f]` reports `function`, and `function` is a primitive type usable in annotations. A quoted data list beginning with `"fn"` is still a list.

Functions retain their original immutable code and lexical environment, including lazy source bindings. Passing a function never emits or reparses source, and creating one does not execute its free bindings. Different constructor calls retain independent environments. Functions compare structurally by original code and captured lexical bindings (code retains scalar kinds, quoting and container evaluation order); recursive bindings are compared with cycle detection. This is not a proof that two functions compute the same result. Function errors retain their original source spans and module identity.

`[try, expr, handler]` evaluates `expr`; if that fails, `handler` (a function name or inline `fn`) is called with the error as a map (`kind`, `message`, `path`, `line`, `column`, as in saved error state) and its result replaces the failure. Without a failure the handler never runs. `[assert, cond]` / `[assert, cond, "message"]` returns `null` when `cond` is `true`, otherwise fails with an `assertion` error carrying the message. `[raise, kind, message]` / `[raise, kind, message, data]` fails with a user-chosen `kind` string (a non-empty string), message and optional plain-value `data` payload, blamed on the `raise` expression; it has the same shape as a builtin error and `data` appears in the error map. `[raise, error]` re-raises a caught error map (so a handler can match the kinds it expects with `cond` and re-raise the rest rather than swallow them).

`[let, {name: expr, ...}, body]` evaluates `body` with the bindings in scope. Bindings see each other (mutually, as in a map) and are evaluated only when used; a self-dependent binding is a `cycle`.

**Iteration** takes a function (a name or inline `fn`) first, like every actor: `[map, f, list]`, `[filter, f, list]` (`f` must return a boolean), `[fold, f, init, list]` (`f` takes accumulator then element), `[each, f, list]` (calls `f` for each element, returns `null`; useful once effects exist). `[until, done, step, init]` repeats `state = step(state)` until `done(state)` is `true` and returns the state; more than 100000 steps is a `limit` error.

**Evaluation order**: arguments left to right, all of them, except the forms that choose: `if`, `cond`, `and`, `or` (lazy), `fn` (body not run until called), `let` (bindings on use). Memoization is per node outside function bodies; inside a body every call re-evaluates.

`[finally, expr, cleanup]` evaluates `expr`, then evaluates `cleanup` whether `expr` succeeds or fails. A successful cleanup discards its own value and preserves the original result or error. A failing cleanup replaces the original result or error. Cleanup is an expression, not a callback; use a function call when needed. This applies to evaluation errors; parse and import failures happen before evaluation.

## Immutable machines and inspection

`[vm, function]` requires a zero-argument function and returns a **ready** child without executing its body. The child retains the function's lexical environment and has independent memoization, limits and cycle guards. `[step, child]` returns a new version after one engine transition. Retaining `child` preserves its original meaning; advancing two branches is independent. Stepping a finished, failed or signaled machine returns the same logical state. `[type, child]` reports `vm`, also a primitive type for annotations and membership checks.

`[inspect, child]` returns ordinary read-only data with `status`, current `location`, `continuations`, `environments`, `intermediate`, `active` cycle guards, `memoized` results, `limits`, `outcome` and a named `objects` table for code, functions and child machines. Source references contain `source` and a `path` list whose segments are string keys or integer indices. `[]`, `[""]`, `["a/b"]`, `["0"]` and `[0]` are distinct paths. Inspection performs no child execution. The named program object includes immutable source code; unquoted symbols are represented as `{symbol: name}` data so quoting is preserved without executable serialization tricks.

A result containing functions or machines is printed as an inspection graph:

```yaml
status: finished
root: {ref: object0}
objects:
  object0: # named function or machine, with readable references to other objects
    kind: function
    # code, lexical environment, program and location follow
```

Ordinary data retains its compact representation. An inspection graph keeps the normal `root` or `error` projection and places its named `objects` table beside it in the state envelope, so recursive lexical references stay finite and ordinary user keys cannot collide with metadata. It is **not a resumable file format**; loading this output yields ordinary data, not reconstructed functions or machines. Machines compare by logical execution state rather than allocation identity. Transition counts and continuation details are implementation details: wait for a status or observable condition rather than counting steps.

The Rust API has the same semantics: `Vm::step(&self) -> Vm`, `Vm::inspect()`, and `run(&mut self)` over the same transitions. The dispatcher represents calls, iteration progress, annotations, pending refinement predicates and cleanup explicitly. Nested child advancement uses the same iterative scheduler. See `tests/cases/vm_parent_child.yaml` for a parent that retains an earlier child, inspects both versions and independently finishes two branches. Disk snapshots, restoration validation, reversibility-promise syntax, external effects and debugger UI are separate future work.

## Type rules

Mixed integer/float equality and ordering compare exactly, including integers beyond the exact integer range of floating-point numbers. Arithmetic promotion still rounds to floating-point precision.

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

### Type-time evaluation

`[type_time, expression]` marks an explicit pure region in ordinary evaluation. It can compute a type or any intermediate value with the same lexical bindings, functions, iteration and errors as other code. Both `run` and `check` use this form identically. The command selects how errors are collected; only `type_time` enters the purity boundary. Uncalled bodies and unchosen branches are not checked.

| Mechanism | Evaluation | File inputs | State metadata |
| --- | --- | --- | --- |
| `run` | Stops at the first uncaught failure | Permitted by the loader policy | A single `error`; type-time failures also put `phase: type` on the state |
| `check` | Collects independent failures | Same loader policy as `run` | `mode: check`; each entry in `errors` retains its own phase |
| `[type_time, expr]` | Evaluates an explicit pure region under either command | Requires the entire linked program to have no file imports, even unused ones | Errors raised in the region carry `phase: type` |

The region shares the caller's recursion, iteration, node-visit and produced-value budgets; nesting never resets them. It recomputes dependencies in a phase-local cache, so an earlier caught run-time error cannot masquerade as a type-time diagnostic. Errors raised inside carry `phase: type` alongside their usual kind, message, location, payload and call sites. `try` can catch them inside or outside the region. Under `run`, an uncaught failure also puts `phase: type` on the saved VM state and stops evaluation. Under `check`, it retains that phase in the `errors` list while independent entries are evaluated; the state has no aggregate phase. Leaving the region, including after a caught error, restores the caller's phase.

Only supplied YAML source and embedded `std:` modules are admitted. A program with any file import is conservatively rejected when it enters a type-time region, even if that import is unused or already evaluated. The catchable `effect` error names the file import and blames its node. Source loading/linking still happens before evaluation: missing files and other link errors remain load errors outside `try`. To compute a reproducible type, provide dependencies as source data or embedded modules; filesystem module contents are not accepted as pinned inputs. Current evaluation has no clock, randomness, environment, network or other host-effect operations. File loading is the sole host-input boundary and occurs outside the region; future effectful actors must be denied before executing in type time.

`[parents, class]` returns immediate parent names in declaration order. `[open_fields, class]` returns a map of required field names to their declared specs that still need values. Fixed, defaulted and optional fields are omitted; a realized class has no open fields. This uses the same field interpretation as realization. Both forms, and `fields`, return ordinary data suitable for further type construction.

## Libraries and imports

`[import, "helpers.yaml"]` loads a Helix document. Relative paths resolve from the importing file's directory; stdin resolves from the working directory. All imports are linked at load time, including imports inside functions and unchosen branches. The specifier is a literal string, quoted or plain, never an evaluated expression. Missing files, unknown standard modules and circular dependencies fail before execution, outside `try` handlers.

A library normally exports a map. Every key is public; its definitions resolve lexically inside the library and cannot see the importing program's names. Files are identified by canonical path and linked once per program. Imports can themselves import other libraries. There is no package registry, version resolver or implicit search path.

```yaml
math: [import, std:math]
helpers: [import, "helpers.yaml"]
answer: [math.square, 6]
```

Dotted symbols select map members (`helpers.constant`, `helpers.nested.function`); an exact lexical binding containing dots takes precedence. Use imported functions as call heads or iteration callbacks, such as `[map, math.square, [1, 2, 3]]`. Missing members in argument position are `unbound` errors; an unresolved call head remains a data list, following ordinary expression detection. Module errors include `file`; their `path`, `line`, and `column` refer to that file.

Imports evaluate to their document's value. Finished output embeds the evaluated library value through the same named object graph used for other functions. Imported functions keep direct references to their original module code and scope. This output is inspection data; keep the original sources and dependencies to run the program again.

Standard libraries are Helix source embedded in the executable:

| Import | Exports |
|--------|---------|
| `std:set` | `from(xs)`, `contains(xs, value)`, `insert(xs, value)`, `remove(xs, value)`, `union(left, right)`, `intersection(left, right)`, `difference(left, right)`, `is_subset(left, right)`, `is_disjoint(left, right)`, `equals(left, right)`, `symmetric_difference(left, right)` |
| `std:stack` | `push(xs, value)`, `peek(xs, fallback)`, `pop(xs)`; top at the end |
| `std:queue` | `enqueue(xs, value)`, `peek(xs, fallback)`, `dequeue(xs)`; front at index zero |
| `std:map` | `merge(left, right)` (shallow, right wins), `remove(source, key)` (one string key; missing is harmless), `select(source, keys)`, `omit(source, keys)` |
| `std:machine` | Abstract machine classes `host`, `packages`, `files`, `services`, `users`, their combination `machine`, the `port` refinement, and fleet checks `hostnames(fleet)` / `unique_hostnames(fleet)` over realized configs |
| `std:types` | `from_fields(specs)`, `without_field(t,name)`, `optional_field(spec)`, `optional_fields(t)` |
| `std:math` | `number` (numeric type), `abs(n)`, `sign(n)`, `min(a,b)`, `max(a,b)`, `clamp(n,lo,hi)`, `square(n)` |
| `std:list` | `numbers` (numeric list type), `sum(xs)`, `product(xs)`, `reverse(xs)`, `range(n)` (integers from zero while less than `n`, bounded by the iteration limit), `unique(xs)` (first occurrence, structural/numeric equality), `sort_by(xs, before)` (stable; strict ordering predicate), plus [access, traversal and grouping helpers](#list-helpers) |

`std:set`, `std:stack`, and `std:queue` use ordinary lists; `[]` is empty and `len` gives size (normalize sets first). Set operations use structural equality, including numeric equality, and remove duplicates while retaining first encounter order. Union visits left then right; intersection and difference retain left order. `contains` and `is_subset` accept duplicates. Stack and queue `peek` require an explicit fallback, returned only when empty; null and false remain ordinary stored values. Empty `pop` and `dequeue` return `[]`. Every operation is immutable and checks list arguments. List copies and repeated membership scans suit small inputs; set operations can take quadratic time and bounded folds retain the runtime iteration limit.

`std:group` exports `by(xs, key)`: group a list into a map using a function returning a string key. Empty and Unicode keys are valid. Keys follow first occurrence order, and each group's items retain input order. The key function runs once per item; callback failures propagate. List and function arguments are checked even for empty input. Immutable map/list copying can take quadratic time and total allocations; use small inputs within the runtime iteration limit.

Standard modules dogfood ordinary function annotations. `std:math` exports `number` (`integer | float`); all its helpers take and return numbers, with `sign` returning an integer. `std:list` reuses that type for its `numbers` list type and for `sum`/`product`, which validate every element before folding. `reverse`, `unique` and `sort_by` require lists and return lists; `sort_by` requires a function even for an empty input. Its comparator must return a boolean and define a strict ordering; the annotation checks that it is a function, not that ordering law. `range` takes an integer and returns a list of integers; negative inputs still return an empty list. Contract failures are structured type errors at the call site with argument names and nested violation paths.

`std:map` preserves insertion order: merging retains existing left-key positions and appends new right keys in right-key order; removal preserves survivor order. Nested values are replaced whole, including null. `merge` and `remove` require map arguments; removal requires a string key even for an empty map. They use bounded folds and immutable map copies, which can cost quadratic time and total allocations; use small inputs.

`std:map.select(source, keys)` retains requested keys; `omit(source, keys)` excludes them. Both preserve source key order, ignore missing and repeated requested keys, and preserve values including null and false. The source must be a map and keys must be a list of strings, even for empty inputs. Membership scans and immutable copies suit small inputs.

`std:set.is_disjoint(left, right)` tests whether the inputs share no values. `equals(left, right)` compares set membership, ignoring order and duplicates. `symmetric_difference(left, right)` returns unique left-only values followed by unique right-only values, preserving encounter order within each side. These helpers use the same structural/numeric equality, list contracts and resource limits as the existing set operations.

### Pinned dependencies

The minimal package model is **vendored paths pinned by the consuming repository**.
Copy a dependency's Helix sources into `vendor/NAME/` and commit them with the
program. Import its entry file directly, for example
`helpers: [import, "vendor/helpers/main.yaml"]`. Internal imports remain relative
to that entry file, so preserve the dependency's directory layout and vendor its
transitive dependencies too. Two dependencies may keep different versions in
separate directories; canonical file paths determine module identity.

Keep an `UPSTREAM.md` beside each dependency recording its source URL, exact commit
(or release plus a content checksum), license, entry file, and any local patches.
This file is human-maintained provenance, not a runtime manifest. The committed
source bytes are the pin; recording a revision alone does not fetch or verify them.
To update, replace those sources from the chosen upstream revision, review the
source diff and license, update provenance, run the consuming program's tests,
and commit the changes together. Avoid mutable branches and absolute import paths.
No dependency download, registry lookup, or Git executable is needed to run a
vendored program. Copying the whole program directory preserves its dependencies;
copying just the main file or emitted state need not.

`tests/cases/import_vendored.yaml` is a runnable example; the `imports` suite also
checks relocation, independent versions, and failure when a dependency is absent.
The embedded `std:` library is pinned separately by the Helix executable version.

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

`./manage.py check FILE` (or `helix check FILE`) evaluates the current pure language and collects independent errors across container entries in source order, deduplicating failures reached through multiple dependents. It uses the same annotations, evaluator, and import policy as `run`; it does not enter type time or statically analyze uncalled function bodies or unchosen branches. Output includes `mode: check`, with `root` on success or an `errors` list on failure. Only errors raised inside explicit `type_time` regions carry `phase: type`; repeated failures of the same dependency are deduplicated within each phase. This replaces the former top-level `phase: type` marker on check output; consumers should inspect `mode` for collection mode and each error’s `phase` for type-time origin. Exit codes are 0 for success, 1 for program errors and 2 for usage or input-file failures. Successful ordinary-data roots retain the YAML round-trip contract; function/machine roots use the named inspection contract.

### Classes, machines and drift

A class is a type that can be extended. `[class, name, record_type, values?]` declares one; `[extend, name, parents, own_record, values]` and `[inherit, name, parents, values]` build children. Parents contribute fields and fixed values in order. Where parents disagree on a field declaration or fixed value, the merge raises a `conflict` error naming both sources unless the child resolves it. A redeclared field must be a subtype of every parent's declaration. Lists and maps are never merged: a child takes them whole or conflicts. `[constrain, class, fn]` adds a whole-record predicate (cross-field invariants); constraints are unioned across parents and a failure is a `type` violation at `/`.

`[realize, class]` fills defaults and checks fixed values against the declared types. A class with required fields still unfilled is `abstract`: realizing it raises an `abstract` error whose `data.missing` lists each field and `required_by`. Every class records provenance: for each field, the declaring class (`type`) and where the value came from (`value`, e.g. `default of X`), so "who set this" is answered from state.

Machine state is compared as data. `[drift, class, actual]` returns `{missing, changed, extra}`; `[converge, class, actual]` returns ordered `set` steps and is empty when the machine conforms. Neither acts: applying a plan is a separate effectful step that does not exist yet. A `[secret, ref]` handle (`{type: secret, ref}`) is the only value a `secret`-typed field accepts; inline strings are rejected.

Collection helpers used to build such data: `[list, ...]` and `[dict, k, v, ...]` evaluate their arguments (plain strings in literal lists and maps are data, not symbols), `values` returns a map's values and `duplicates` returns repeated list members.

## Error kinds

`permission` (a denied file import), `effect` (a host input forbidden at type time), `import` (module loading or circular dependency), `parse`, `unbound`, `type`, `conflict` (incompatible class parents), `abstract` (unfilled required fields on realization), `arity`, `cycle`, `overflow`, `divide_by_zero`, `missing`, `range`, `conversion`, `unsupported`, `depth` (syntax/value, call, dependency or import nesting), `limit` (work/data/graph or iteration budgets), `assertion` (failed `assert`), plus any kind chosen by user code with `raise`.

## Testing in Helix

Import `t: [import, std:test]`. Testing policy runs as ordinary Helix; reports are
serializable values and failed cases do not abort the remaining cases.

```yaml
t: [import, std:test]
report:
  - t.suite
  - - {name: arithmetic, run: [fn, [], [t.equal, [add, 2, 3], 5]]}
    - {name: expected_failure, run: [fn, [], [t.throws, [fn, [], [div, 1, 0]], "divide_by_zero"]]}
checked: [t.require_pass, report]
```

| Function | Contract |
| --- | --- |
| `equal(actual, expected)` | Return null on structural/numeric equality; otherwise raise `assertion` with both values in `data`. |
| `capture(thunk)` | Call a zero-argument function and return `{outcome: passed, value: ...}` or `{outcome: failed, error: ...}`. |
| `throws(thunk, kind)` | Require an error of that kind and return the original error map. Normal return or a different kind raises `assertion`. |
| `fields_equal(actual, expected)` | Assert a top-level subset of map fields. Extra actual fields are allowed; missing differs from null, and nested values compare exactly. |
| `throws_matching(thunk, expected)` | Require an error matching the expected fields, including mandatory `kind`; return the original error with source details intact. |
| `case(spec)` | Execute `{name, run}`; retain its name and optional `input` alongside the captured result. |
| `suite(cases)` | Return `{total, passed, failed, results}` in source order. Empty suites pass. Normal return passes, including false or null; assert explicitly to test a condition. |
| `require_pass(report)` | Return a clean report or raise `test_failure` with the entire report as `data`. |
| `table(rows, verify)` | Each `{name, input}` row runs `verify(input)` independently; retain the input for reproduction. |
| `property(name, samples, predicate)` | Check each supplied sample; false raises an assertion, and predicate errors remain failures. |
| `compare(name, samples, left, right)` | Compare both algorithms on each sample with `equal`; callback errors are failures. |
| `combine(reports)` | Sum child counts and concatenate leaf results in encounter order; empty input yields an empty passing report. |
| `is_failure(result)` | Test whether a captured result has outcome `failed`; shared by suite counting and selection. |
| `failures(report)` | Return failed result records with replay inputs and errors intact. |
| `tagged(cases, tag)` | Select case records in source order; absent tags behave as an empty list. |
| `rerun(cases, report)` | Run source cases whose names failed in the report, preserving source order. Duplicate names select every matching source case; prefer unique names. |
| `product(axes)` | Enumerate a finite Cartesian product in axis order, retaining duplicates. No axes yields `[[]]`; an empty axis yields `[]`. |
| `lists(alphabet, max_length)` | Enumerate every list of length zero through the nonnegative bound, in length then alphabet order. Negative bounds raise `range`. |

Use `[t.product, [list, xs, ys]]` for bounded pairs and `[t.lists, [-1, 0, 1], 3]`
for exhaustive short lists. Use `[t.suite, [t.tagged, cases, "fast"]]` to run a tag,
then `[t.rerun, cases, report]` to retry failed names. `combine` retains leaf failures
without wrapping entire reports in captured callbacks. Supply
fixture factories as functions so each case gets its own immutable environment.
Expected states are ordinary table data compared with `equal`; this is semantic
value comparison, not byte-for-byte YAML comparison. `tests/cases/dogfood_testing.yaml`
exercises these flows, runner self-tests, nested reports, cleanup precedence,
retained counterexamples and deterministic candidate shrinking. `std_test_failure`
proves a report can produce the usual VM error and exit code 1.

Only supplied samples are tested; these helpers do not prove a property. Work and
immutable copying grow with the supplied cases and captured fixtures. Keep samples
small and within runtime call/iteration limits. Generated list domains grow exponentially; generators do not bypass VM resource bounds. Pure tests cannot observe process
crashes, timeouts or side effects. Discovery, file golden comparison, serialization
fixpoints and crash detection remain in `./manage.py test` until the required
file/process/parsing services exist. The same golden runner tests this module;
there is no competing test entrypoint.


### Debugging and pure failed-run recovery

Function failures carry an optional `calls` list, ordered from the innermost call outward.
Each entry records the call-site `path`, `line`, `column`, optional imported `file`,
and the function declaration's `function` path, `function_line`, `function_column`,
and optional `function_file`. Call-site and declaration files are independent: a call
in the main program can invoke a function declared in an imported module. Missing
file fields refer to the main program; coordinates are 1-indexed. Use these fields
for navigation rather than parsing `message`. This includes callback, parameter-contract,
and result-contract failures. Errors caught with `try` have the same diagnostic fields.
Re-raising with `[raise, error]` records the complete supplied error map under `cause`;
the new error blames the re-raise site and accumulates its own call chain. Repeating this
preserves each earlier cause, payload and location.

`./manage.py recover program.yaml` runs a pure program and, on evaluation failure,
adds a `recovery` string containing editable YAML source to the error state.
Save stdout to a state file, repair that string, then run
`./manage.py resume state.yaml`. A failed resume emits another recovery checkpoint.
Completed ordinary-data root-map fields become literal saved values; unfinished fields and fields containing functions or machines retain their original expressions. Completed ordinary-data siblings are not computed again. Other root
shapes restart as a whole. An unfinished function or iteration restarts from its
beginning: this is a pure recovery boundary, not suspended stack restoration.
Changes to inputs do not invalidate already completed fields; remove or restore those
fields' expressions explicitly when they should be computed again.

File-based checkpoints retain the original absolute `recovery_file` for relative
imports. Imported sources are reloaded and must still be available. Parse/link failures
have no executable checkpoint. Recovery uses the same result/error and object-inspection contracts as `run` and `check`.
Recovery applies only to the current pure evaluator; effectful retry will require the
complete continuation and effect-state contract in charter section 2.

### YAML toolkit

Import `std:yaml` for `parse(source)`, `dump(value)`, `valid(source)`,
`get_in(value, path)` and `set_in(value, path, replacement)`. The strict builtin
primitives are `yaml_parse` and `yaml_dump`. Parsing reads data only: symbols,
expression-shaped lists and imports are never executed. It accepts exactly one
nonempty document using Helix's scalar inference, string map keys, duplicate-key
checks and syntax resource limits. An initial UTF-8 BOM is accepted. Literal
non-printable source characters are rejected; escaped character values remain
valid and serialize with escapes. Aliases, explicit tags and container keys
are unsupported. Tags are rejected rather than silently discarding their semantics.
Malformed input raises `parse`; its message includes the location within the
input text, while the error span identifies the Helix call argument. `valid`
returns false for rejected YAML; its source must still be a string.

`dump` uses the VM's block YAML emitter, preserving insertion order and quoting
ambiguous strings. Comments, original formatting and anchors are not retained.
Ordinary data values round-trip through `parse(dump(value))`. Dedicated function and VM values are outside this data-only contract; use machine inspection for their named object views.
Paths are lists of string map keys and integer list indices. An empty path
selects or replaces the root. Updates copy containers along the path, require
existing intermediate containers, and permit a new final map key. Missing paths
and invalid indices use the ordinary `get`/`set` errors.

## Resource bounds and confined loading

Each VM shares budgets across calls, iterations and error recovery: 2,000,000 evaluated node visits and 128,000,000 produced value units, with at most 1,000,000 units in any one value. A unit is a scalar/container/key plus the UTF-8 bytes of strings; nested values and memoized copies count again. Budget exhaustion is `limit`, not rejection of a value by a declared type. A graph, including imported code, has at most 100,000 nodes. Computed ordinary-data values obey the parser's graph-node cap so their results stay re-readable; map keys contribute text units, not graph nodes. Syntax and produced values have at most 64 nested containers; import chains at most 64 modules; active evaluation at most 512 nodes. These guards return `depth` or `limit` with source blame. The existing 200-call and 100,000-iteration limits still apply. Execution is iterative in both CLI and library use; suspended calls are explicit continuations rather than host call frames.

`helix sandbox ROOT FILE` (also `./manage.py runtime sandbox ROOT FILE`) loads the main file explicitly supplied by the caller and restricts all file imports to canonical paths below ROOT. ROOT `-` permits only embedded `std:` modules. Path traversal, sibling-prefix and symlink escapes are rejected as `permission` errors at the import argument, including transitive and uncalled imports. Ordinary `run` retains trusted loading. Library callers use `Vm::load_sandboxed(source, optional_file, roots)`; empty roots deny file imports. These permissions are host configuration and are never granted by program/state data.

This is an import capability boundary, not operating-system isolation: a host concurrently replacing directory ancestors can race path authorization. Arbitrarily large source files are read before evaluation limits apply, and these budgets are not a byte-exact heap quota. File writes, network and process effects do not exist yet; they must acquire explicit capabilities before being added. Recovery output is source data and conveys no permission grant.

Retained functions and machines can form deep object graphs even with shallow source. Cleanup releases those graphs iteratively on the dropping host thread, including during thread-local teardown; releasing them must not overflow the host stack. Programs retaining growing capture graphs can still use proportionate memory under the existing work and data budgets.
