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
| string  | `hello`, `"hello, world"`  | strings are literal regardless of scalar style |
| boolean | `true`, `false`            | |
| null    | `null`, `~`                | |
| list    | `[1, 2, 3]`, block `- x`   | elements are evaluated |
| map     | `{a: 1}`, block `k: v`     | string keys; values are evaluated in canonical key order |
| function | `{$call: [fn, [x], {$call: [add, {$ref: x}, 1]}]}` | direct code and lexical environment; distinct from list data |
| vm | `{$call: [vm, {$call: [fn, [], 42]}]}` | immutable execution state; created ready without running its body |

## Expressions

Calls, references and literal boundaries are explicit singleton maps:

```yaml
x: 2
answer: {$call: [add, {$ref: x}, 1]}
names: [add, x, 1]
reserved_data: {$literal: {$call: [add, 1, 2]}}
```

`$call` contains a nonempty sequence: an actor name or computed function expression followed by arguments. `$ref` contains a nonempty lexical name; it resolves through enclosing maps and function environments, with dotted names selecting members. `$literal` returns its complete payload as data, including any nested reserved maps. Only singleton maps with these keys select syntax. Malformed reserved forms fail explicitly. Other maps evaluate their values; sequences evaluate their elements as data sequences, never implicit calls. Scalars are literal regardless of quoting style. All runtime maps, including constructed maps and codec results, use lexicographic Unicode string-key order. Canonical mapping traversal makes source field order irrelevant; use a sequence or explicit control flow when ordering matters.

Legacy actor-first sequences require explicit offline conversion with `./manage.py runtime migrate-syntax --write FILE`; the runtime has no legacy fallback.

The supported YAML subset has string mapping keys, finite scalars, sequences and mappings. Duplicate keys, cyclic aliases, custom tags and non-string keys are rejected. Acyclic aliases expand under resource bounds at the occurrence’s lexical scope; standard scalar tags (`!!str`, `!!int`, `!!float`, `!!bool`, `!!null`) and collection tags (`!!seq`, `!!map`) are supported. Integers include hexadecimal and octal notation within signed 64-bit bounds; floats remain finite. This is a semantic model over YAML representation nodes, not support for every YAML construct. JSON source has identical language meaning to equivalent YAML. Reformatting scalar styles or permuting mapping entries cannot change a program's meaning.

Ordinary result data is emitted without executable wrappers. To reexecute arbitrary result data, wrap the whole value in `{$literal: ...}` so reserved singleton maps stay data. Function and machine inspection graphs are not executable serialization.

References resolve to the nearest enclosing map defining the name; inner definitions shadow outer ones. Function parameters and `let` bindings are in scope inside their bodies. A value that depends on itself is a `cycle` error.

Values are immutable: `set` and `append` return new values.

## Functions and local names

`{$call: [fn, [params], {$ref: body}]}` declares a function; give it a name by putting it in a map: `{double: {$call: [fn, [n], {$call: [mul, {$ref: n}, 2]}]}}`. A `$call` whose actor resolves to such a function invokes it: `{$call: [double, 4]}`. Arguments are evaluated first (left to right) and bound to the parameters; a wrong argument count is an `arity` error blamed on the call. A function evaluates to a dedicated function value; inspecting or printing it does not turn its code into a data list.

Scope is **lexical**, through parent links: a body sees its parameters, then the maps enclosing the *definition* (not the caller), so a function closes over the object graph it is written in. Recursion works through the function's own name. Calls nested deeper than 200 fail with a `depth` error blamed on the call. Functions are values: pass or return them, bind them with `let`, and store them in lists and maps. `{$call: [call, {$ref: function_expression}, {$ref: arg}, ...]}` calls any computed function; a named binding holding a computed function can also head a call. Iteration and `try` accept computed functions too. `{$call: [type, {$ref: f}]}` reports `function`, and `function` is a primitive type usable in annotations. A data list beginning with `fn` is still a list, regardless of quoting.

Functions retain their original immutable code and lexical environment, including lazy source bindings. Passing a function never emits or reparses source, and creating one does not execute its free bindings. Different constructor calls retain independent environments. Functions compare structurally by original code and captured lexical bindings (code retains scalar kinds and sequence order; scalar style and mapping presentation order do not participate); recursive bindings are compared with cycle detection. This is not a proof that two functions compute the same result. Function errors retain their original source spans and module identity.

`{$call: [try, {$ref: expr}, {$ref: handler}]}` evaluates `expr`; if that fails, `handler` (a function name or inline `fn`) is called with the error as a map (`kind`, `message`, `path`, `line`, `column`, as in saved error state) and its result replaces the failure. Without a failure the handler never runs. `{$call: [assert, {$ref: cond}]}` / `{$call: [assert, {$ref: cond}, message]}` returns `null` when `cond` is `true`, otherwise fails with an `assertion` error carrying the message. `{$call: [raise, {$ref: kind}, {$ref: message}]}` / `{$call: [raise, {$ref: kind}, {$ref: message}, {$ref: data}]}` fails with a user-chosen `kind` string (a non-empty string), message and optional plain-value `data` payload, blamed on the `raise` expression; it has the same shape as a builtin error and `data` appears in the error map. `{$call: [raise, {$ref: error}]}` re-raises a caught error map (so a handler can match the kinds it expects with `cond` and re-raise the rest rather than swallow them).

`{$call: [let, {name: value}, {$ref: body}]}` evaluates `body` with the bindings in scope. Bindings see each other (mutually, as in a map) and are evaluated only when used; a self-dependent binding is a `cycle`.

**Iteration** takes a function (a name or inline `fn`) first, like every actor: `{$call: [map, {$ref: f}, {$ref: list}]}`, `{$call: [filter, {$ref: f}, {$ref: list}]}` (`f` must return a boolean), `{$call: [fold, {$ref: f}, {$ref: init}, {$ref: list}]}` (`f` takes accumulator then element), `{$call: [each, {$ref: f}, {$ref: list}]}` (calls `f` for each element, returns `null`; useful for ordered effects). `{$call: [until, {$ref: done}, {$ref: step}, {$ref: init}]}` repeats `state = step(state)` until `done(state)` is `true` and returns the state; more than 100000 steps is a `limit` error.

**Evaluation order**: map fields by canonical string-key order, sequence elements and arguments left to right, all of them, except the forms that choose: `if`, `cond`, `and`, `or` (lazy), `fn` (body not run until called), `let` (bindings on use). Memoization is per node outside function bodies; inside a body every call re-evaluates.

`{$call: [finally, {$ref: expr}, {$ref: cleanup}]}` evaluates `expr`, then evaluates `cleanup` whether `expr` succeeds or fails. A successful cleanup discards its own value and preserves the original result or error. A failing cleanup replaces the original result or error. Cleanup is an expression, not a callback; use a function call when needed. This applies to evaluation errors; parse and import failures happen before evaluation.

## Immutable machines and inspection

`{$call: [vm, {$ref: function}]}` requires a zero-argument function and returns a **ready** child without executing its body. The child retains the function's lexical environment and has independent memoization, limits and cycle guards. `{$call: [step, {$ref: child}]}` returns a new version after one engine transition. Retaining `child` preserves its original meaning; advancing two branches is independent. Stepping a finished, failed or signaled machine returns the same logical state. `{$call: [type, {$ref: child}]}` reports `vm`, also a primitive type for annotations and membership checks.

`{$call: [inspect, {$ref: child}]}` returns ordinary read-only data with `status`, current `location`, `continuations`, `environments`, `intermediate`, `active` cycle guards, `memoized` results, `limits`, `outcome` and a named `objects` table for code, functions and child machines. Source references contain `source` and a `path` list whose segments are string keys or integer indices. `[]`, `[""]`, `["a/b"]`, `["0"]` and `[0]` are distinct paths. Inspection performs no child execution. The named program object includes immutable source code with explicit call and reference forms; scalar quoting has no language meaning.

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

The Rust API has the same semantics: `Vm::step(&self) -> Vm`, `Vm::inspect()`, and `run(&mut self)` over the same transitions. The dispatcher represents calls, iteration progress, annotations, pending refinement predicates and cleanup explicitly. Nested child advancement uses the same iterative scheduler. See `tests/cases/vm_parent_child.yaml` for a parent that retains an earlier child, inspects both versions and independently finishes two branches. Disk snapshots, restoration validation, reversibility-promise syntax and debugger UI are separate future work. Explicit host effects are described below.

## Type rules

Mixed integer/float equality and ordering compare exactly, including integers beyond the exact integer range of floating-point numbers. Arithmetic promotion still rounds to floating-point precision.

No implicit conversion, with one exception: **numeric promotion**. `int` op `int` is an `int`; if either operand is a `float` the result is a `float`. Everything else needs an explicit `str`, `int` or `float`.

## Builtins

**Arithmetic** (numbers): `add`, `mul` (2 or more args, left fold), `sub`, `div`, `mod` (exactly 2), `neg` (1). Integer `div` and `mod` truncate toward zero. Dividing by zero is `divide_by_zero`; integer or float overflow is `overflow`.

**Comparison**: `eq`, `ne` work on any values structurally (numbers compare by value, so `1` equals `1.0`). `lt`, `le`, `gt`, `ge` compare two numbers or two strings.

**Logic** (booleans only): `not`; `and`, `or` take 2 or more args, are *lazy* (stop at the first `false` / `true`, never evaluating the rest) and return a boolean. `{$call: [if, {$ref: cond}, {$ref: then}, {$ref: else}]}` evaluates only the chosen branch. `{$call: [cond, {$ref: c1}, {$ref: v1}, {$ref: c2}, {$ref: v2}, ..., {$ref: default}]}` tests the conditions in order and evaluates only the value of the first `true` one, else the default (a missing default is an `arity` error).

**Collections** (strings index by character):

| Form | Result |
|------|--------|
| `{$call: [len, {$ref: x}]}` | length of a string, list or map |
| `{$call: [concat, {$ref: a}, {$ref: b}, ...]}` | all strings, or all lists |
| `{$call: [slice, {$ref: x}, {$ref: start}, {$ref: end}]}` | half-open range of a string or list |
| `{$call: [get, {$ref: x}, {$ref: key}]}` / `{$call: [get, {$ref: x}, {$ref: key}, {$ref: default}]}` | list index or map key; `missing` error without a default |
| `{$call: [set, {$ref: x}, {$ref: key}, {$ref: value}]}` | replaces an existing list index, or inserts/replaces a map key |
| `{$call: [append, {$ref: list}, {$ref: v}, ...]}` | list with values added at the end |
| `{$call: [keys, {$ref: map}]}` | list of keys |
| `{$call: [has, {$ref: x}, {$ref: y}]}` | map: key present; list: element present (by `eq`); string: substring present |

**Conversion and inspection**: `{$call: [str, {$ref: scalar}]}`, `{$call: [int, {$ref: x}]}` (floats truncate toward zero; strings must parse), `{$call: [float, {$ref: x}]}`, `{$call: [type, {$ref: x}]}` (`integer`, `float`, `string`, `boolean`, `null`, `list`, `map`, `function`). A type is a value: a primitive name or a declaration map. `{$call: [is, {$ref: x}, {$ref: t}]}` tests `x` against any declared type (or `any`); an unknown name is a `type` error.

## First-class types

`any` is the top type: every value inhabits it. `never` is the bottom type: no value inhabits it, including `null`, and it is a subtype of every type. `null` itself remains inhabited by the null value. A `list_of never` accepts only the empty list; a `map_of never` accepts only the empty map. Adding `never` to a union does not change its accepted values. Subtyping remains conservative: it does not discover every empty type or normalize declarations.

Types use ordinary value evaluation: `let`, function arguments and results, collections, conditionals and iteration all work with primitive names, records, unions, enums, literals and refinements. Constructors validate computed type arguments and blame the offending argument when it is not a type.

```yaml
list_type: {$call: [fn, [t], {$call: [list_of, {$ref: t}]}]}
strings: {$call: [list_type, {$ref: string}]}
at_least: {$call: [fn, [minimum], {$call: [refine, {$ref: integer}, {$call: [fn, [n], {$call: [ge, {$ref: n}, {$ref: minimum}]}]}]}]}
positive: {$call: [at_least, 1]}
valid: {$call: [is, 5, {$ref: positive}]}
```

The pure type constructors are `list_of`, `map_of`, `record` (alternating field names and specs), `optional`, `default`, `union`, `enum`, `literal` and `refine`. Records are closed. `fields` returns a record or class's field specs as a map, including optional/default wrappers; `with_field` adds or replaces a record field. The `std:types` module derives records using ordinary functions: `from_fields(specs)` reconstructs a record from reflection, `without_field(t, name)` drops a field (an absent name is unchanged), and `optional_fields(t)` makes required fields optional while retaining existing optional fields and defaults. These functions use canonical field order and return fresh records; projecting a class's fields does not copy its parents, fixed values or provenance. They fold over the existing checked constructors, with no separate type evaluator. `{$call: [subtype, {$ref: a}, {$ref: b}]}` checks the existing structural type algebra conservatively: arbitrary predicates are not proved equivalent. `{$call: [violations, {$ref: value}, {$ref: t}]}` returns every nested violation; `{$call: [expect, {$ref: value}, {$ref: t}]}` returns the value or raises a structured type error containing those violations.

Subtyping uses identity and `any` as the top type. Lists and maps are covariant in their element type. A union is below a type when every alternative is below it; a type is below a union when one alternative covers it. An enum or literal is below a type when every listed value inhabits it. A refinement is below its base; proving a value belongs to a refinement still runs its predicate. Closed records admit no fields outside the target record, each source field must have a subtype of the corresponding target field, and every required target field must be required in the source. Defaults do not change membership. Unproved relationships return `false`; no theorem prover runs for arbitrary predicates.

`eq` on types is structural equality of their declaration values: record field order is ignored, list order (including union members and enum alternatives) is significant, and defaults and captured predicates participate. This is declaration equality, not equality of the sets of accepted values. Types retain their readable declaration maps in saved state and round-trip through the same evaluator.

### Type-time evaluation

`{$call: [type_time, {$ref: expression}]}` marks an explicit pure region in ordinary evaluation. It can compute a type or any intermediate value with the same lexical bindings, functions, iteration and errors as other code. Both `run` and `check --evaluate` use this form identically. The command selects how errors are collected; only `type_time` enters the purity boundary. Uncalled bodies and unchosen branches are not checked.

| Mechanism | Evaluation | File inputs | State metadata |
| --- | --- | --- | --- |
| `run` | Stops at the first uncaught failure | Permitted by the loader policy | A single `error`; type-time failures also put `phase: type` on the state |
| `check --evaluate` | Collects independent failures | Same loader policy as `run` | `mode: check`; each entry in `errors` retains its own phase |
| `{$call: [type_time, {$ref: expr}]}` | Evaluates an explicit pure region under either command | Requires the entire linked program to have no file imports, even unused ones | Errors raised in the region carry `phase: type` |

The region shares the caller's recursion, iteration, node-visit and produced-value budgets; nesting never resets them. It recomputes dependencies in a phase-local cache, so an earlier caught run-time error cannot masquerade as a type-time diagnostic. Errors raised inside carry `phase: type` alongside their usual kind, message, location, payload and call sites. `try` can catch them inside or outside the region. Under `run`, an uncaught failure also puts `phase: type` on the saved VM state and stops evaluation. Under `check --evaluate`, it retains that phase in the `errors` list while independent entries are evaluated; the state has no aggregate phase. Leaving the region, including after a caught error, restores the caller's phase.

Only supplied YAML source and embedded `std:` modules are admitted. A program with any file import is conservatively rejected when it enters a type-time region, even if that import is unused or already evaluated. The catchable `effect` error names the file import and blames its node. Source loading/linking still happens before evaluation: missing files and other link errors remain load errors outside `try`. To compute a reproducible type, provide dependencies as source data or embedded modules; filesystem module contents are not accepted as pinned inputs. Explicit effect requests for clock, randomness, environment, network and other host operations are rejected before yielding in type time. File loading occurs outside the region.

`{$call: [parents, {$ref: class}]}` returns immediate parent names in declaration order. `{$call: [open_fields, {$ref: class}]}` returns a map of required field names to their declared specs that still need values. Fixed, defaulted and optional fields are omitted; a realized class has no open fields. This uses the same field interpretation as realization. Both forms, and `fields`, return ordinary data suitable for further type construction.

## Libraries and imports

`{$call: [import, helpers.yaml]}` loads a Helix document. Relative paths resolve from the importing file's directory; stdin resolves from the working directory. All imports are linked at load time, including imports inside functions and unchosen branches. The specifier is a literal string, quoted or plain, never an evaluated expression. Missing files, unknown standard modules and circular dependencies fail before execution, outside `try` handlers.

A library normally exports a map. Every key is public; its definitions resolve lexically inside the library and cannot see the importing program's names. Files are identified by canonical path and linked once per program. Imports can themselves import other libraries. There is no package registry, version resolver or implicit search path.

```yaml
math: {$call: [import, 'std:math']}
helpers: {$call: [import, helpers.yaml]}
answer: {$call: [math.square, 6]}
```

Dotted references select map members (`helpers.constant`, `helpers.nested.function`); an exact lexical binding containing dots takes precedence. Use imported functions as call heads or iteration callbacks, such as `{$call: [map, {$ref: math.square}, [1, 2, 3]]}`. Missing members in argument position are `unbound` errors; an unresolved call actor is an `unbound` error. Module errors include `file`; their `path`, `line`, and `column` refer to that file.

Imports evaluate to their document's value. Finished output embeds the evaluated library value through the same named object graph used for other functions. Imported functions keep direct references to their original module code and scope. This output is inspection data; keep the original sources and dependencies to run the program again.

Standard libraries are Helix source embedded in the executable:

| Import | Exports |
|--------|---------|
| `std:set` | `from(xs)`, `contains(xs, value)`, `insert(xs, value)`, `remove(xs, value)`, `union(left, right)`, `intersection(left, right)`, `difference(left, right)`, `is_subset(left, right)`, `is_disjoint(left, right)`, `equals(left, right)`, `symmetric_difference(left, right)` |
| `std:stack` | `push(xs, value)`, `peek(xs, fallback)`, `pop(xs)`; top at the end |
| `std:queue` | `enqueue(xs, value)`, `peek(xs, fallback)`, `dequeue(xs)`; front at index zero |
| `std:map` | `merge(left, right)` (shallow, right wins), `remove(source, key)` (one string key; missing is harmless), `select(source, keys)`, `omit(source, keys)` |
| `std:machine` | Abstract machine classes `host`, `packages`, `files`, `services`, `users`, their combination `machine`, the `port` refinement, and fleet checks `hostnames(fleet)` / `unique_hostnames(fleet)` over realized configs |
| `std:fuzz` | `plan(source)` (ordered damaged-source slice plans) |
| `std:text` | `split(source, separator)` (literal, non-overlapping), `join(parts, separator)` |
| `std:diff` | `compare(old, new)` (shallow configuration changes) |
| `std:validation` | `batch(values, validate)` (indexed validation and transformation reports) |
| `std:dependency` | `plan(tasks)` (stable dependency batches and blocked-task report) |
| `std:types` | `from_fields(specs)`, `without_field(t,name)`, `optional_field(spec)`, `optional_fields(t)` |
| `std:math` | `number` (numeric type), `abs(n)`, `sign(n)`, `min(a,b)`, `max(a,b)`, `clamp(n,lo,hi)`, `square(n)` |
| `std:list` | `numbers` (numeric list type), `sum(xs)`, `product(xs)`, `reverse(xs)`, `range(n)` (integers from zero while less than `n`, bounded by the iteration limit), `unique(xs)` (first occurrence, structural/numeric equality), `sort_by(xs, before)` (stable; strict ordering predicate), plus [access, traversal and grouping helpers](#list-helpers) |

`std:set`, `std:stack`, and `std:queue` use ordinary lists; `[]` is empty and `len` gives size (normalize sets first). Set operations use structural equality, including numeric equality, and remove duplicates while retaining first encounter order. Union visits left then right; intersection and difference retain left order. `contains` and `is_subset` accept duplicates. Stack and queue `peek` require an explicit fallback, returned only when empty; null and false remain ordinary stored values. Empty `pop` and `dequeue` return `[]`. Every operation is immutable and checks list arguments. List copies and repeated membership scans suit small inputs; set operations can take quadratic time and bounded folds retain the runtime iteration limit.

`std:group` exports `by(xs, key)`: group a list into a map using a function returning a string key. Empty and Unicode keys are valid. Keys use canonical string-key order, and each group's items retain input order. The key function runs once per item; callback failures propagate. List and function arguments are checked even for empty input. Immutable map/list copying can take quadratic time and total allocations; use small inputs within the runtime iteration limit.

`std:fuzz.plan` describes each line prefix followed by deletion of that line, then character prefixes at offsets 0, 5, 10 and so on (strictly before the end). It preserves duplicates and line terminators, recognizes CRLF as one boundary and also recognizes LF, CR, vertical tab, form feed, U+001C–U+001E, U+0085, U+2028 and U+2029. Empty input yields no variants; offsets count Unicode scalars. This pure policy supports host-supervised malformed-input testing: process crashes and timeouts must still be detected outside Helix. Each variant is a list of `[start, end]` source-slice spans; hosts concatenate those slices to materialize one damaged input at a time. The plan preserves mutation policy in Helix without retaining quadratic output text. Scanning immutable strings and accumulating spans can still consume quadratic work; runtime budgets apply.

`std:text.split` preserves empty fields, including the one field in an empty source, and rejects an empty separator with a `range` error. Matching uses Unicode scalar indices. `join` accepts a list of strings and a string separator; an empty list produces an empty string and an empty separator concatenates directly. Arguments are checked even for empty inputs. Scanning and immutable copies can take quadratic time and allocations; runtime iteration and work budgets apply.

`std:diff.compare` accepts two maps and returns `added` and `removed` key lists plus `changed` records containing `key`, `before` and `after`. Added, removed and changed keys each follow canonical string-key order. Missing keys differ from explicit null; nested values are compared structurally as whole values. Lookup and immutable copying suit small configurations and may be quadratic.

`std:validation.batch` collects independent callback outcomes using the report policy in `std:test`. Its report has `total`, `passed`, `failed` and `results`; each result retains `input: {index, value}`, the name `validation`, and either a passed value or the original structured error. Callbacks receive one value in input order. Normal returns, including false and null, pass; rejection must raise. Failures do not stop later inputs. Retaining inputs and immutable copies suits small batches; iteration and work budgets apply.

`std:dependency.plan` accepts a map from task names to lists of prerequisite names. It returns `order`, `pending`, `missing` and `status` (`finished` or `blocked`). Ready tasks run in stable batches following canonical task-name order. Blocked tasks remain in `pending`; missing references are reported as task/dependency records in canonical task-name order, including duplicates. A blocked graph with no missing references contains a cycle; the report retains its blocked dependents too. Repeated scans and immutable copies may be cubic on long chains; runtime budgets and iteration limits apply.

Standard modules dogfood ordinary function annotations. `std:math` exports `number` (`integer | float`); all its helpers take and return numbers, with `sign` returning an integer. `std:list` reuses that type for its `numbers` list type and for `sum`/`product`, which validate every element before folding. `reverse`, `unique` and `sort_by` require lists and return lists; `sort_by` requires a function even for an empty input. Its comparator must return a boolean and define a strict ordering; the annotation checks that it is a function, not that ordering law. `range` takes an integer and returns a list of integers; negative inputs still return an empty list. Contract failures are structured type errors at the call site with argument names and nested violation paths.

`std:map` uses canonical string-key order for merge and removal results; right-hand values win during merging. Nested values are replaced whole, including null. `merge` and `remove` require map arguments; removal requires a string key even for an empty map. They use bounded folds and immutable map copies, which can cost quadratic time and total allocations; use small inputs.

`std:map.select(source, keys)` retains requested keys; `omit(source, keys)` excludes them. Both use canonical string-key order, ignore missing and repeated requested keys, and preserve values including null and false. The source must be a map and keys must be a list of strings, even for empty inputs. Membership scans and immutable copies suit small inputs.

`std:set.is_disjoint(left, right)` tests whether the inputs share no values. `equals(left, right)` compares set membership, ignoring order and duplicates. `symmetric_difference(left, right)` returns unique left-only values followed by unique right-only values, preserving encounter order within each side. These helpers use the same structural/numeric equality, list contracts and resource limits as the existing set operations.

### Pinned dependencies

The minimal package model is **vendored paths pinned by the consuming repository**.
Copy a dependency's Helix sources into `vendor/NAME/` and commit them with the
program. Import its entry file directly, for example
`{helpers: {$call: [import, vendor/helpers/main.yaml]}}`. Internal imports remain relative
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

Import `{l: {$call: [import, 'std:list']}}`. Helpers take the list first; predicates and transforms are ordinary functions. All helpers are pure and preserve input order unless sorting explicitly changes it.

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
| `group_by(xs, key_for)` | Group into a map by string keys, using `std:group.by`; key order is canonical and buckets retain input order. |
| `frequencies(xs)` | Count strings, including empty strings, in canonical string-key order. |
| `sort(xs)` | Ascending numeric or string order, using `sort_by`. Mixed incomparable values are type errors when compared. |

Callbacks are checked as functions even for empty lists. Predicates must return booleans when called. Searches stop after the first decisive element; fallback arguments are evaluated eagerly like other function arguments. Immutable list/map copies and repeated scans can be quadratic, including sorting, grouping and deduplication. These helpers target small inputs and obey the runtime iteration limits; none recurses in proportion to input length.

`./manage.py std` lists modules; `./manage.py std math` prints a module's source. The binary also accepts `helix std [module]`.

`./manage.py check --evaluate FILE` (or `helix check --evaluate FILE`) evaluates the current pure language and collects independent errors across sequence entries in order and mapping fields by canonical key, deduplicating failures reached through multiple dependents. It uses the same annotations, evaluator, and import policy as `run`; it does not enter type time or statically analyze uncalled function bodies or unchosen branches. Output includes `mode: check`, with `root` on success or an `errors` list on failure. Only errors raised inside explicit `type_time` regions carry `phase: type`; repeated failures of the same dependency are deduplicated within each phase. This replaces the former top-level `phase: type` marker on check output; consumers should inspect `mode` for collection mode and each error’s `phase` for type-time origin. Exit codes are 0 for success, 1 for program errors and 2 for usage or input-file failures. Successful ordinary-data roots retain the YAML round-trip contract; function/machine roots use the named inspection contract.

### Classes, machines and drift

A class is a type that can be extended. `[class, name, record_type, values?]` declares one; `{$call: [extend, {$ref: name}, {$ref: parents}, {$ref: own_record}, {$ref: values}]}` and `{$call: [inherit, {$ref: name}, {$ref: parents}, {$ref: values}]}` build children. Parents contribute fields and fixed values in order. Where parents disagree on a field declaration or fixed value, the merge raises a `conflict` error naming both sources unless the child resolves it. A redeclared field must be a subtype of every parent's declaration. Lists and maps are never merged: a child takes them whole or conflicts. `{$call: [constrain, {$ref: class}, {$ref: fn}]}` adds a whole-record predicate (cross-field invariants); constraints are unioned across parents and a failure is a `type` violation at `/`.

`{$call: [realize, {$ref: class}]}` fills defaults and checks fixed values against the declared types. A class with required fields still unfilled is `abstract`: realizing it raises an `abstract` error whose `data.missing` lists each field and `required_by`. Every class records provenance: for each field, the declaring class (`type`) and where the value came from (`value`, e.g. `default of X`), so "who set this" is answered from state.

Machine state is compared as data. `{$call: [drift, {$ref: class}, {$ref: actual}]}` returns `{missing, changed, extra}`; `{$call: [converge, {$ref: class}, {$ref: actual}]}` returns ordered `set` steps and is empty when the machine conforms. Neither acts: applying a plan is a separate effectful step that does not exist yet. A `{$call: [secret, {$ref: ref}]}` handle (`{type: secret, ref}`) is the only value a `secret`-typed field accepts; inline strings are rejected.

Collection helpers used to build such data: `{$call: [list, ...]}` and `{$call: [dict, {$ref: k}, {$ref: v}, ...]}` evaluate their arguments (plain strings in literal lists and maps are data, not symbols), `values` returns a map's values and `duplicates` returns repeated list members.

## Error kinds

`permission` (a denied file import), `effect` (a host input forbidden at type time), `import` (module loading or circular dependency), `parse`, `unbound`, `type`, `conflict` (incompatible class parents), `abstract` (unfilled required fields on realization), `arity`, `cycle`, `overflow`, `divide_by_zero`, `missing`, `range`, `conversion`, `unsupported`, `depth` (syntax/value, call, dependency or import nesting), `limit` (work/data/graph or iteration budgets), `assertion` (failed `assert`), plus any kind chosen by user code with `raise`.

## Testing in Helix

Import `{t: {$call: [import, 'std:test']}}`. Testing policy runs as ordinary Helix; reports are
serializable values and failed cases do not abort the remaining cases.

```yaml
t: {$call: [import, 'std:test']}
report: {$call: [t.suite, [{name: arithmetic, run: {$call: [fn, [], {$call: [t.equal, {$call: [add, 2, 3]}, 5]}]}}, {name: expected_failure, run: {$call: [fn, [], {$call: [t.throws, {$call: [fn, [], {$call: [div, 1, 0]}]}, divide_by_zero]}]}}]]}
checked: {$call: [t.require_pass, {$ref: report}]}
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
| `product(axes)` | Enumerate a finite Cartesian product in axis order, retaining duplicates. No axes yields `{$call: [[]]}`; an empty axis yields `[]`. |
| `lists(alphabet, max_length)` | Enumerate every list of length zero through the nonnegative bound, in length then alphabet order. Negative bounds raise `range`. |

Use `{$call: [t.product, {$call: [list, {$ref: xs}, {$ref: ys}]}]}` for bounded pairs and `{$call: [t.lists, [-1, 0, 1], 3]}`
for exhaustive short lists. Use `{$call: [t.suite, {$call: [t.tagged, {$ref: cases}, fast]}]}` to run a tag,
then `{$call: [t.rerun, {$ref: cases}, {$ref: report}]}` to retry failed names. `combine` retains leaf failures
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
Re-raising with `{$call: [raise, {$ref: error}]}` records the complete supplied error map under `cause`;
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
primitives are `yaml_parse` and `yaml_dump`. Parsing reads data only: reserved call/reference maps and imports are never executed. It accepts exactly one
nonempty document using Helix's scalar inference, string map keys, duplicate-key
checks and syntax resource limits. An initial UTF-8 BOM is accepted. Literal
non-printable source characters are rejected; escaped character values remain
valid and serialize with escapes. Acyclic aliases and standard scalar/collection tags are supported. Cyclic aliases, custom tags and non-string keys are rejected rather than silently discarding their semantics.
Malformed input raises `parse`; its message includes the location within the
input text, while the error span identifies the Helix call argument. `valid`
returns false for rejected YAML; its source must still be a string.

`dump` uses the VM's block YAML emitter, using canonical string-key order and quoting
ambiguous strings. Comments, original formatting and anchors are not retained.
Ordinary data values round-trip through `parse(dump(value))`. Dedicated function and VM values are outside this data-only contract; use machine inspection for their named object views.
Paths are lists of string map keys and integer list indices. An empty path
selects or replaces the root. Updates copy containers along the path, require
existing intermediate containers, and permit a new final map key. Missing paths
and invalid indices use the ordinary `get`/`set` errors.

### JSON and CSV toolkits

Import `std:json` or `std:csv` for `parse(source)` and `dump(value)`;
the corresponding strict primitives are `json_parse`, `json_dump`, `csv_parse`
and `csv_dump`. All are pure, data-only operations usable inside `type_time`.
JSON parsing requires one complete JSON value, uses canonical object key order,
and uses the last value for a duplicate key. Integer tokens must fit signed
64-bit integers; decimal/exponent tokens must fit finite floats. JSON dumping
uses the existing compact JSON output contract, including a final newline.
Functions and machines cannot be dumped as data. JSON strings never execute.

CSV is a list of rows, each a nonempty list of strings; there is no implicit
header or numeric conversion. Parsing accepts ragged rows, LF/CRLF/CR record
endings, quoted commas and newlines, and doubled quotes. Characters after a
closing quote and quotes inside unquoted fields are rejected. Empty input is
zero rows; a blank line is one empty field; a trailing comma adds an empty
field. Dumping uses CRLF record endings and quotes empty fields and fields
containing commas, quotes or newlines. Empty rows and non-string cells raise
`type`; malformed input raises `parse`. Valid rows round-trip exactly through
`parse(dump(rows))`. Parser graph/nesting and evaluator value limits apply.

## Resource bounds and confined loading

Each VM shares budgets across calls, iterations and error recovery: 2,000,000 evaluated node visits and 128,000,000 produced value units, with at most 1,000,000 units in any one value. A unit is a scalar/container/key plus the UTF-8 bytes of strings; nested values and memoized copies count again. Budget exhaustion is `limit`, not rejection of a value by a declared type. A graph, including imported code, has at most 100,000 nodes. Computed ordinary-data values obey the parser's graph-node cap so their results stay re-readable; map keys contribute text units, not graph nodes. Syntax and produced values have at most 64 nested containers; import chains at most 64 modules; active evaluation at most 512 nodes. These guards return `depth` or `limit` with source blame. The existing 200-call and 100,000-iteration limits still apply. Execution is iterative in both CLI and library use; suspended calls are explicit continuations rather than host call frames.

`helix sandbox ROOT FILE` (also `./manage.py runtime sandbox ROOT FILE`) loads the main file explicitly supplied by the caller and restricts all file imports to canonical paths below ROOT. ROOT `-` permits only embedded `std:` modules. Path traversal, sibling-prefix and symlink escapes are rejected as `permission` errors at the import argument, including transitive and uncalled imports. Ordinary `run` retains trusted loading. Library callers use `Vm::load_sandboxed(source, optional_file, roots)`; empty roots deny file imports. These permissions are host configuration and are never granted by program/state data.

This is an import capability boundary, not operating-system isolation: a host concurrently replacing directory ancestors can race path authorization. Arbitrarily large source files are read before evaluation limits apply, and these budgets are not a byte-exact heap quota. File writes, network and process effects use the explicit caller grants described below. Recovery output is source data and conveys no permission grant.

### Host effects and explicit grants

`{$call: [effect, {$ref: operation}, {$ref: payload}]}` yields an explicit request after evaluating its two arguments. Type time rejects effects before yielding. `Vm::run()` stops with `status: signaled`; the state contains `pending_effect` (identity, operation and payload). A host supplies a success value or structured failure through `resolve_effect`. Completed requests and outcomes remain in `effects`; resolving an already completed request does not execute it again. This is in-memory continuation support, not disk snapshot restoration.

Host applications use `Vm::signal(event)` to interrupt an active machine and
`resume_signal()` to continue it. Events are bounded ordinary YAML data, visible
under `signal` while interrupted. A pending request survives interruption;
resolving it does not clear the interrupt. The bundled host leaves interrupted
machines alone. These are explicit host event APIs, not installed OS signal
handlers. Retained machine branches remain independent: explicitly handing an
older pending branch to a host can perform its request again. Request IDs are
local to a branch, not global deduplication keys.

An effect failure does not promise rollback of partial external changes. The
runtime never automatically retries requests, and failed machines with completed
effect records do not emit pure recovery source that could repeat those actions.

`helix perform [grants] file.yaml [-- arguments...]` runs requests through the bundled host. It emits program stdout to stdout and full state to stderr. Source comes from a file so stdin remains available to the program. `run` and `exec` never acquire host capabilities. Use `perform --help` to discover grants. Repeated root, name, executable and origin flags accumulate; grants belong to the caller, never to source or saved state.

| Operation | Payload | Result | Grant |
| --- | --- | --- | --- |
| `args` | `null` | List of arguments after the source filename | None |
| `exit` | Integer 0–255 | `null`; sets successful process exit code after evaluation finishes | None |
| `file_read` | Path string | UTF-8 text | `--allow-read ROOT` |
| `file_write` | `{path: string, text: string}` | `null` | `--allow-write ROOT` |
| `env` | Variable name | String, or `null` if absent | `--allow-env NAME` |
| `stdin` | `null` | UTF-8 text read until EOF | `--allow-stdin` |
| `stdout` | Text string | `null` | `--allow-stdout` |
| `command` | `{program: string, args: list_of_strings}` | `{code, stdout, stderr}` | `--allow-command EXECUTABLE` |
| `http` | Absolute HTTP(S) URL string | `{status, body}` | `--allow-http ORIGIN` |
| `time` | `null` | Unix epoch milliseconds | `--allow-time` |
| `random` | `null` | Nonnegative 63-bit integer | `--allow-random` |

File imports under `perform` require the same `--allow-read` roots (embedded standard modules remain available). Relative file-effect paths resolve against the host working directory; imports retain source-relative resolution. File roots are canonicalized; existing symlinks and parents of new files must remain within a granted root. These checks are import-style confinement, not protection against concurrent filesystem changes or an OS sandbox. Commands use argument vectors without an implicit shell, inherit the host environment and working directory, and receive closed stdin. An executable grant authorizes that executable's ambient access, including subprocesses; granting a shell authorizes shell commands. Nonzero command exits are result data; spawn, encoding and host failures are catchable errors. The `exit` effect selects the eventual successful exit code and does not skip cleanup or later evaluation.

HTTP uses the installed `curl` executable for GET requests, disables curl configuration, proxies, URL globbing and redirects, and restricts protocols to HTTP(S). An origin grant includes scheme, authority and explicit port exactly as written. HTTP error statuses remain result data. Process output, file text and HTTP responses are limited to 1 MiB; processes have a 10-second deadline and HTTP requests an 8-second deadline. Descendant processes are not contained or killed as a process group. Stdin waits for EOF.

`--fixed-time MILLISECONDS` fixes the clock response. `--seed INTEGER` selects a reproducible random sequence, advanced once per request; both still require their grants. Without these options the host reads the system clock and `/dev/urandom`. The deterministic generator is intended for tests and replay inputs, not cryptographic randomness. Effect request/outcome histories expose nondeterministic inputs for inspection; full fresh-process replay of active continuations remains future work. Capability failures occur before host actions and can be handled with `try`/`finally` through the ordinary evaluator.

Retained functions and machines can form deep object graphs even with shallow source. Cleanup releases those graphs iteratively on the dropping host thread, including during thread-local teardown; releasing them must not overflow the host stack. Programs retaining growing capture graphs can still use proportionate memory under the existing work and data budgets.
