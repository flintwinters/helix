# Helix Language Reference

Core syntax, evaluation, types and execution semantics live here. See the
[standard library reference](LIBRARY.md) for reusable helpers and testing, and
the [host operations reference](HOST.md) for resource lifecycles and caller grants.

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

The former `closure(bindings, fn)` builtin has been removed: use
`{$call: [let, {offset: 2}, {$call: [fn, [x], {$call: [add, {$ref: x}, {$ref: offset}]}]}]}`.
This retains the same lazy bindings and captured function environment through the
ordinary `let` and `fn` forms. An unbound `closure` call now raises `unbound`; a
user-defined function with that name is an ordinary eager call with no special
binding scope. Update explicit source by replacing the old builtin head with
`let`. The offline legacy syntax migrator converts recognized actor-first
`closure` forms to `let`; it does not rewrite already-explicit source.

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

The checked constructors `list_of`, `map_of`, `optional`, `default`, `union`,
`enum`, `literal`, `record`, `with_field` and `secret` assemble their results
through `std:type_descriptor` in the ordinary evaluator. The runtime retains
the shared type/field checker and argument diagnostics; annotations and defaults
do not use a second checker. The module's `build(actor, args)` is the assembly
implementation, not a replacement for the checked public constructors. Its
immutable record updates copy growing maps, so large records can incur quadratic
copying costs and all policy execution shares the caller's resource budgets.
`with_field` validates the replacement spec and every retained field through the
same field parser as `record`. A malformed retained field raises `type` at the
input record argument; replacing that malformed field itself is allowed because
its old spec is discarded. Migration: repair or remove malformed sibling specs
before extending a hand-built record. Validation checks declaration shape without
running refinement predicates or rechecking default values against their types.
Repeated extensions also reparse growing field sets; they do not certify or cache
the validity of arbitrary descriptor data.

```yaml
list_type: {$call: [fn, [t], {$call: [list_of, {$ref: t}]}]}
strings: {$call: [list_type, {$ref: string}]}
at_least: {$call: [fn, [minimum], {$call: [refine, {$ref: integer}, {$call: [fn, [n], {$call: [ge, {$ref: n}, {$ref: minimum}]}]}]}]}
positive: {$call: [at_least, 1]}
valid: {$call: [is, 5, {$ref: positive}]}
```

The pure type constructors are `list_of`, `map_of`, `record` (alternating field names and specs), `optional`, `default`, `union`, `enum`, `literal`, `refine`, `function_of` and `pure_function_of`. Records are closed. `fields` returns a record or class's field specs as a map, including optional/default wrappers; `with_field` adds or replaces a record field. The `std:types` module derives records using ordinary functions: `from_fields(specs)` reconstructs a record from reflection, `without_field(t, name)` drops a field (an absent name is unchanged), and `optional_fields(t)` makes required fields optional while retaining existing optional fields and defaults. These functions use canonical field order and return fresh records; projecting a class's fields does not copy its parents, fixed values or provenance; `redeclare(class, parents)` recovers a class instead: it re-runs `extend` with the fields and values that provenance attributes to the class itself and preserves the complete original constraint list, using the existing subtype proof to reject loss of inherited requirements (then `realize` if it was realized), so constructor-produced descriptors are recovered `eq` to the original when given the original parent classes and raises the usual `type` or `conflict` error otherwise. They fold over the existing checked constructors, with no separate type evaluator. `{$call: [subtype, {$ref: a}, {$ref: b}]}` checks the existing structural type algebra conservatively: arbitrary predicates are not proved equivalent. `{$call: [violations, {$ref: value}, {$ref: t}]}` returns every nested violation; `{$call: [expect, {$ref: value}, {$ref: t}]}` returns the value or raises a structured type error containing those violations.

Subtyping uses conservative internal type identity and `any` as the top type. Predicate identity requires the same retained source and exact captured values, including numeric representations; independently written but equivalent predicates may remain unproved. Bind and reuse a predicate when it is intended to represent the same obligation. Lists and maps are covariant in their element type. A union is below a type when every alternative is below it; a type is below a union when one alternative covers it. Enums and literals accept values by structural equality, including exact mixed integer/float equality. Subtyping must cover every representation equal to each listed value, rather than checking only the representation written in the declaration. Scalar alternatives and structural collection checks are bounded; proofs involving arbitrary predicates over representation-varying collections may be declined. A refinement is below its base; proving a value belongs to a refinement still runs its predicate. Closed records admit no fields outside the target record, each source field must have a subtype of the corresponding target field, and every required target field must be required in the source. Defaults do not change membership. Unproved relationships return `false`; no theorem prover runs for arbitrary predicates.

For example, `literal(1)` accepts both `1` and `1.0`, so it is below `integer | float` but not below `integer` alone. The same rule applies inside lists and maps. Code that previously used an integral numeric literal to narrow an integer field should use `refine(integer, predicate)` with an equality predicate when the integer representation is required, or permit both numeric types. This corrects subtype proofs; literal membership and numeric equality are unchanged.

`{$call: [function_of, [param_types...], result_type]}` describes a function: `{type: function, params: [...], result: T}`, and the bare `function` is the type of every function. Parameter and result types may be function types, so higher-order signatures nest. A function inhabits a function type when its declared annotations prove it: the same number of parameters, each target parameter type a subtype of the declared one (contravariant), and the declared result a subtype of the target result (covariant). An unannotated parameter is `any` and accepts every argument; an unannotated result is `any` and promises nothing, so it only satisfies a result of `any`. Membership evaluates the annotations in the function's own lexical environment without calling it, through the same predicate-aware engine requests; a failing annotation is a violation, not a crash. Subtyping between function types follows the same variance, and every function type is below `function`. Violations name both signatures, for example `expected function (integer) -> integer, got function (any) -> any`. Annotated parameters and results therefore check callbacks when a function is called (`types_function_signatures`). `{$call: [pure_function_of, [param_types...], result_type]}` is the same type with an effect contract, `{..., pure: true}`: the function must also be provably free of effect requests, judged from its source without calling it. The analysis scans everything the function can reach by name (its body and annotations, the functions and bindings it mentions, imported modules it names; called or not, so the answer is conservative). It is not provable when the scan finds `effect` or `step`, or a call whose callee it cannot see: a parameter not annotated with a `pure_function_of` type, or a computed callee. Recursion and iteration over visible pure functions are provable. Predicates a value carries into `is`/`expect` run when the check does and are not tracked. A pure function type is below the same type without the contract, never the reverse, and violations say `(not provably pure)` when purity alone was missing (`types_function_purity`). Annotate callback parameters with a `pure_function_of` type to write higher-order functions that stay provably pure, such as those required in `type_time`.

Violation lists use depth-first traversal. Lists visit increasing indices; maps
visit canonical string-key order. Records visit declared fields in canonical key
order (including missing required fields), then unexpected fields in canonical
key order. Nested violations stay together at their field or index. A rejected
union emits one violation at the checked value, with alternatives named in
declaration order, rather than concatenating each alternative's violations.
Refinements check the base first and only run their predicate if the base passes;
this also applies when the base is another refinement. Refinements can wrap the
existing type constructors and serve as collection elements or required,
optional and defaulted fields. Class membership also enforces fixed values and
constraints, as described below. `types_violation_order` and `types_refinement_composition` fix
these ordering and composition contracts.

`eq` on types is structural equality of their declaration values: record field order is ignored, list order (including union members and enum alternatives) is significant, and defaults and captured predicates participate. This is declaration equality, not equality of the sets of accepted values. In particular, language-equal closures can distinguish integer and float captures in their bodies. Subtyping, inherited field agreement and constraint deduplication therefore use stricter predicate identity; `eq` itself is unchanged. Reconstruction preserves distinct original predicates rather than subtracting them with language equality. Types retain their readable declaration maps in saved state and round-trip through the same evaluator.

### Type-time evaluation

`{$call: [type_time, {$ref: expression}]}` marks an explicit pure region in ordinary evaluation. It can compute a type or any intermediate value with the same lexical bindings, functions, iteration and errors as other code. Both `run` and `check --evaluate` use this form identically. The command selects scheduling and error collection; only `type_time` enters the purity boundary. Preflight discovers selected closed body regions as described below; it does not validate every uncalled function body.

| Mechanism | Evaluation | File inputs | State metadata |
| --- | --- | --- | --- |
| `run` | Stops at the first uncaught failure | Permitted by the loader policy | A single `error`; type-time failures also put `phase: type` on the state |
| `check --evaluate` | Collects independent failures | Same loader policy as `run` | `mode: check`; each entry in `errors` retains its own phase |
| `{$call: [type_time, {$ref: expr}]}` | Evaluates an explicit pure region under either command | Uses immutable linked modules admitted by the loader policy | Errors raised in the region carry `phase: type` |

The region shares the caller's recursion, iteration, node-visit and produced-value budgets; nesting never resets them. It recomputes dependencies in a phase-local cache, so an earlier caught run-time error cannot masquerade as a type-time diagnostic. Errors raised inside carry `phase: type` alongside their usual kind, message, location, payload and call sites. `try` can catch them inside or outside the region. Under `run`, an uncaught failure also puts `phase: type` on the saved VM state and stops evaluation. Under `check --evaluate`, it retains that phase in the `errors` list while independent entries are evaluated; the state has no aggregate phase. Leaving the region, including after a caught error, restores the caller's phase.

Preflight: before the first run-time step, `run` evaluates every region outside a `try` operand, a `vm` thunk, a function body and an import: mapping entries, sequence items, computed call heads, call and iteration arguments, `let` bindings and bodies, and every operand of `if`, `cond`, `and`, `or` and `finally`, including unchosen branches and unreferenced bindings. A failing region stops the run before any effect is requested, even when an effect sorts earlier. A function body is entered when the function is reached (a name resolving to a `fn` in a scanned position, or a `fn` literal used as a call head or operand) and only regions the source dependency scan proves parameter-independent are hoisted. This proof is conservative: it follows named bindings without substituting call arguments, so even a helper called with constants can keep a region deferred when the helper body reads parameters. Deferred regions still record their actual pre-existing inputs; a region that reads none leaves no input entry. Open regions run when the call happens, `try` still catches failures of regions it encloses, and a function nothing reaches stays unchecked. Results of root-level regions are memoized and reused by the run; body regions are recomputed by each call.

The scan follows source structure without executing a callee selector: both branches of a computed head are scanned like other branches. Parameter dependencies anywhere in that head keep its region open, including dependencies through lexical bindings or captures. Regions in function bodies not reached by this source scan keep ordinary call order; a returned function can still be discovered when its literal appears in a scanned operand. `check --evaluate` instead traverses independent root entries with the same evaluator; it does not run the whole-program preflight schedule or inspect unreachable branches merely to collect their phase errors. A reached region has the same purity, inputs and error contract under either command.

The retained scheduling boundaries serve distinct existing workloads:

| Boundary | Preserved behavior and reason |
| --- | --- |
| `try` operands | Regions run under the installed handler. Provider planning and batch validation need per-input failures as values; hoisting them would bypass recovery. |
| `vm` thunk | The child owns its execution and failure state. A parent can retain and step a failed child without failing itself. |
| Parameter-dependent function region | The region runs with the current call frame and records its inputs. Provider planning depends on supplied observations; a higher-order callback or selector is an input too. |
| Unreached function and import root | Importing reusable policy does not execute every declaration. A statically reached imported function still participates in closed-region discovery. |
| Unchosen branches outside these boundaries | Closed validation runs before earlier effects, even when a branch would be skipped. The existing preflight golden cases fix this deliberate early-validation rule. |

These boundaries retain one evaluator, one purity guard and phase-local memoization; preflight only schedules ordinary evaluation. The `type_time`, `execution` and provider suites cover the resulting interactions. Migration for computed heads: closed failures previously missed by the scan now precede runtime effects; parameter-dependent heads previously hoisted without their frame now run with their arguments. Use `try` when failure must be handled in runtime order, as with other placements. Successful `check --evaluate` runs no longer incidentally execute the run preflight schedule after collection; an unrelated sibling error therefore cannot change branch reachability. Check results retain type-time inputs across independent entries, including captures in memoized closures, under the same shared budgets.

Determinism: a region's result is determined by the complete linked source graph and the run-time values it reads. Effect requests are rejected inside the phase and its dependencies are recomputed purely, so the only run-time data that can enter is a parameter or captured value from a frame that predates the region. The machine records each such value for an outermost region (a function by its source node, since its captured values are recorded when read). The state lists `type_time_inputs: [{location, reads: [{function, name}]}]`; inspection adds the values. Input deduplication preserves ordinary-data representations, including integer versus float, signed zero, and nested values. Exact repeated reads deduplicate; equal numeric values with different representations remain separate entries. The compact summary can therefore repeat a location/name pair whose inspected values differ. A region that reads none leaves no entry, and preflighted regions are always closed. Recording is bounded by the value budget and exceeding it is a `limit` error.

File and embedded `std:` imports use the same pure evaluation boundary. The loader reads, authorizes and links module source before evaluation; evaluating an import reads the retained graph, not the filesystem. Missing files, import cycles and confinement failures remain load errors outside `try`. Reproducibility across fresh loads requires preserving every transitive module source as well as the main program; a path alone does not pin its contents. Existing machines retain their loaded graph when files change. Explicit effect requests for clock, randomness, environment, network and other host operations are rejected before yielding in type time, including requests in imported functions. Import roots are not scanned merely because they are imported. As with local functions, closed regions in a statically reached imported function can be preflighted; other module regions run when reached. Migration: pure regions formerly rejected solely because a file import existed now evaluate normally; callers must not rely on that former `effect` error as an import authorization check. Use loader confinement for that policy.

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

Member lookup traverses ordinary source maps lazily, without evaluating unrelated
fields. When an intermediate binding computes a map, the ordinary evaluator
evaluates that expression before selecting the remaining members. A module can
therefore use `let` to keep implementation bindings internal and return an
explicit map of exports; callers retain the same dotted-reference and call syntax.

Imports evaluate to their document's value. Finished output embeds the evaluated library value through the same named object graph used for other functions. Imported functions keep direct references to their original module code and scope. This output is inspection data; keep the original sources and dependencies to run the program again.

Standard libraries are Helix source embedded in the executable. See the
[standard library catalog](LIBRARY.md#standard-libraries) for exports and contracts.

### Host inventory

See [Host inventory](HOST.md#host-inventory).

### Resource composition

See [Resource composition](HOST.md#resource-composition).

### Resource provider contract

See [Resource provider contract](HOST.md#resource-provider-contract).

### Guarded command resources

See [Guarded command resources](HOST.md#guarded-command-resources).

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

See [List helpers](LIBRARY.md#list-helpers).

## Checking programs

Plain `check` reports `unbound` for an explicit reference or named call whose
lexical starting name is absent. It shares runtime lexical lookup, preserving
exact dotted bindings and function captures; primitive type references and
registered builtin call heads remain valid. It does not execute source or load
imports, and does not infer whether an imported, computed or parameter value
contains a requested member. Literal payloads and parameter-name declarations
are not expressions. Migration: unresolved names that previously passed plain
`check` now fail it, including in uncalled bodies; fix the name or provide the
lexical binding. LSP diagnostics use the same check and source locations.

Plain `check` also runs a small non-executing type check (`src/tooling/analysis/body.rs`) over literals, annotated parameters, arithmetic, comparison, `if`, structural maps, constant-key record `get`/`set`, calls of named functions and literal annotation expressions (including `record` and `with_field`). It traverses computed call heads as well as operands, so annotated inline function bodies and nested `type_time` regions are checked even when the call target itself is unproved. Literal data remains opaque. It compares annotated function bodies with their declared results, call arguments with parameter types, and `type_time` regions for effectful execution. Constructing or returning a function does not execute its body; invoking a visible function does. Computed callees remain unproved, while `pure_function_of` retains its stronger transitive contract. A mismatch whose types share no value, or an `effect` inside a region, is a `type` diagnostic. A mismatch that may or may not hold, or an expression outside the fragment, is listed in the report's `unchecked` list and does not fail the check; the LSP shows only definite errors.

Structural map inference retains known fields even when a sibling expression is unknown. Every written field is present; unknown field types remain `any`. Required fields with incompatible types or missing required fields establish definite failure, while incompatible optional fields may overlap by omission. Dynamic keys, optional-key reads, unsupported callbacks and `$literal` data remain outside this structural fragment. The production test-policy regressions require plain `check` to reject an integer `status` or string `exit_code` without executing orchestration; this does not prove the entire policy or replace independent host validation.

Annotation aliases follow explicit `$ref` lookups without executing them; scalar strings remain literal type names even when a binding has the same spelling. Cycles, more than eight constructor levels, expanded descriptors beyond 4096 values/keys, or expanded strings beyond the runtime value-size limit remain unchecked. Memoized alias resolution prevents repeated source expansion; the descriptor budget also bounds the work passed to the shared type parser. These limits affect analysis only, not runtime type semantics.

`./manage.py check --evaluate FILE` (or `helix check --evaluate FILE`) evaluates the current pure language and collects independent errors across sequence entries in order and mapping fields by canonical key, deduplicating failures reached through multiple dependents. It uses the same annotations, evaluator, and import policy as `run`; it does not enter type time or statically analyze uncalled function bodies or unchosen branches. Output includes `mode: check`, with `root` on success or an `errors` list on failure. Only errors raised inside explicit `type_time` regions carry `phase: type`; repeated failures of the same dependency are deduplicated within each phase. This replaces the former top-level `phase: type` marker on check output; consumers should inspect `mode` for collection mode and each error’s `phase` for type-time origin. Exit codes are 0 for success, 1 for program errors and 2 for usage or input-file failures. Successful ordinary-data roots retain the YAML round-trip contract; function/machine roots use the named inspection contract.

## Classes, machines and drift

A class is a type that can be extended. `{$call: [class, name, record_type, values?]}` declares one; `{$call: [extend, {$ref: name}, {$ref: parents}, {$ref: own_record}, {$ref: values}]}` and `{$call: [inherit, {$ref: name}, {$ref: parents}, {$ref: values}]}` build children. Parents contribute fields and fixed values in order. Where parents disagree on a field declaration or fixed value, the merge raises a `conflict` error naming both sources unless the child resolves it. A redeclared field must be a subtype of every parent's declaration. Lists and maps are never merged: a child takes them whole or conflicts. `{$call: [constrain, {$ref: class}, {$ref: fn}]}` adds a whole-record predicate (cross-field invariants); constraints are unioned across parents and a failure is a `type` violation at `/`. Adding a constraint is declaration-only and marks the returned class abstract until `realize` checks it, even when the input was concrete or the new predicate would pass. The original class remains unchanged. Use `realize(constrain(class, predicate))` before planning with the new class. `constrain` requires a function immediately. A class descriptor may omit `constraints` or provide a list of functions; a malformed list or non-function item is a declaration error, including during `subtype(t, any)` and membership of an empty collection. Valid predicate bodies remain deferred until membership or realization reaches them. Rebuild malformed descriptors with `constrain`; use an empty list or omit the field when there are no constraints.

`{$call: [realize, {$ref: class}]}` fills defaults and checks fixed values against the declared types. A class with required fields still unfilled is `abstract`: realizing it raises an `abstract` error whose `data.missing` lists each field and `required_by`. Every class records provenance: for each field, the declaring class (`type`) and where the value came from (`value`, e.g. `default of X`), so "who set this" is answered from state. Each valued field also carries `value_kind: explicit` or `value_kind: default`; this discriminator determines fixed-value semantics, independently of class names and display labels. Older valued descriptors without this discriminator must be rebuilt from their declarations; the runtime rejects ambiguous or malformed kinds instead of inferring them from labels.

A child's explicit value wins over an inherited default. Equal inherited values retain an explicit fixed-value obligation if any parent supplies one, regardless of parent order. Inherited declarations must agree exactly, including defaults and captured predicates. Among competing explicit values (or defaults when none is explicit), the selected representation must also agree exactly: `1` versus `1.0`, signed zero, nested differences and distinct captures can change a predicate's result despite language equality. Such ambiguity raises `conflict` in either parent order. Resolve value ambiguity with an explicit child value; resolve declaration/default ambiguity by redeclaring the field. An explicit value still takes precedence over a language-equal realized default without requiring matching representations. Fixed-value membership continues to use language equality. Different inherited
defaults are conflicting field declarations; resolve them by explicitly
redeclaring the field and supplying a valid value or default. For a defaulted field without an explicit fixed value, membership only
checks the field's type: it may be absent or contain a different valid value. Realization fills an absent defaulted field; membership never fills
it. `class_defaults` covers this distinction. For collection overrides, use
`concat` to explicitly append lists or `std:map.merge` to explicitly merge maps
before supplying the child value. Map merge is shallow and right-biased; neither
operation happens implicitly during inheritance (`class_collection_overrides`).

Class membership (`is`, `expect`, and `violations`) checks the closed field schema (including field refinements), then every explicitly fixed value (the field must be present and equal; values realization filled from a default are not pinned), then each whole-record constraint on the checked map; constraints run only when the earlier checks pass. A realized class's `values` are members of it (`types_class_membership`). As a subtype, a class is below a record with fitting fields, and below another class only when it also pins every value and carries every constraint of the other.

These operations answer distinct questions over the same class:

| Question | Operation | Contract |
| --- | --- | --- |
| Is a supplied value allowed? | `is`, `violations`, `expect` | Closed fields, explicit pins and reached predicates; no default filling. |
| Can a declaration produce a complete valid configuration? | `realize` (`inherit` also realizes) | Fill defaults, require missing fields, check values and constraints, mark concrete. |
| Is every admitted source value proved acceptable to another type? | `subtype` | Structural rules and conservative predicate identity; unproved relationships return false. |
| Which configured values differ from observations? | `drift`, `converge` | Compare concrete configured values; report/preserve extras and do not rerun predicates. |

A realized default `x: 7` can admit `{}` or `{x: 8}` while planning a change to `7`. Conversely, `{x: 7, extra: keep}` fails closed membership but produces an empty convergence plan. These distinctions are intentional; realization and membership share field/predicate checking, while planning shares the existing data-comparison policy.

Machine state is compared as data. `{$call: [drift, {$ref: class}, {$ref: actual}]}` returns `{missing, changed, extra}`; `{$call: [converge, {$ref: class}, {$ref: actual}]}` returns ordered `set` steps and is empty when all configured values match. Extra observed fields are reported in `drift.extra` but never produce removal steps. An empty plan is therefore not equivalent to membership in the closed record schema. Neither acts: applying a plan is a separate effectful step. The
[`std:provider` contract](HOST.md#resource-provider-contract) supplies an explicit reviewed
application lifecycle for provider callbacks; complete declarative machine
application remains future work. A `{$call: [secret, {$ref: ref}]}` handle (`{type: secret, ref}`) is the only value a `secret`-typed field accepts; inline strings are rejected.

Collection helpers used to build such data: `{$call: [list, ...]}` and `{$call: [dict, {$ref: k}, {$ref: v}, ...]}` evaluate their arguments (plain strings in literal lists and maps are data, not symbols), `values` returns a map's values and `duplicates` returns repeated list members.

`std:convergence.compare(wanted, observed)` and `plan(wanted, observed)` own the
pure comparison and plan policy over maps. The public `drift` and `converge`
builtins check descriptor shape and concreteness and extract class values before calling
these functions through the same evaluator. Planning does not rerun field or whole-record
predicates. Class descriptors are ordinary data, and manually setting `concrete` is
not evidence of validation; use `realize` after editing a descriptor. The module reuses `std:diff`, treats
nested changes as whole-field replacements, and distinguishes absence from null.
Its collection passes and immutable map updates consume the caller's work/data
budgets; this migration does not add observation, application or disk recovery.

## Error kinds

`permission` (a denied file import), `effect` (a host input forbidden at type time), `import` (module loading or circular dependency), `parse`, `unbound`, `type`, `conflict` (incompatible class parents), `abstract` (unfilled required fields on realization), `arity`, `cycle`, `overflow`, `divide_by_zero`, `missing`, `range`, `conversion`, `unsupported`, `depth` (syntax/value, call, dependency or import nesting), `limit` (work/data/graph or iteration budgets), `assertion` (failed `assert`), plus any kind chosen by user code with `raise`.

## Testing in Helix

See [Testing in Helix](LIBRARY.md#testing-in-helix).

## Debugging and pure failed-run recovery

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

See [YAML toolkit](LIBRARY.md#yaml-toolkit).

### JSON and CSV toolkits

See [JSON and CSV toolkits](LIBRARY.md#json-and-csv-toolkits).

## Resource bounds and confined loading

Each VM shares budgets across calls, iterations and error recovery: 2,000,000 evaluated node visits and 128,000,000 produced value units, with at most 1,000,000 units in any one value. A unit is a scalar/container/key plus the UTF-8 bytes of strings; nested values and memoized copies count again. Budget exhaustion is `limit`, not rejection of a value by a declared type. A graph, including imported code, has at most 100,000 nodes. Computed ordinary-data values obey the parser's graph-node cap so their results stay re-readable; map keys contribute text units, not graph nodes. Syntax and produced values have at most 64 nested containers; import chains at most 64 modules; active evaluation at most 512 nodes. These guards return `depth` or `limit` with source blame. The existing 200-call and 100,000-iteration limits still apply. Execution is iterative in both CLI and library use; suspended calls are explicit continuations rather than host call frames.

`helix sandbox ROOT FILE` (also `./manage.py runtime sandbox ROOT FILE`) loads the main file explicitly supplied by the caller and restricts all file imports to canonical paths below ROOT. ROOT `-` permits only embedded `std:` modules. Path traversal, sibling-prefix and symlink escapes are rejected as `permission` errors at the import argument, including transitive and uncalled imports. Ordinary `run` retains trusted loading. Library callers use `Vm::load_sandboxed(source, optional_file, roots)`; empty roots deny file imports. These permissions are host configuration and are never granted by program/state data.

This is an import capability boundary, not operating-system isolation: a host concurrently replacing directory ancestors can race path authorization. Arbitrarily large source files are read before evaluation limits apply, and these budgets are not a byte-exact heap quota. File writes, network and process effects use the [explicit caller grants](HOST.md#host-effects-and-explicit-grants). Recovery output is source data and conveys no permission grant.

Retained functions and machines can form deep object graphs even with shallow source. Cleanup releases those graphs iteratively on the dropping host thread, including during thread-local teardown; releasing them must not overflow the host stack. Programs retaining growing capture graphs can still use proportionate memory under the existing work and data budgets.

### Host effects and explicit grants

See [Host effects and explicit grants](HOST.md#host-effects-and-explicit-grants).

#### Bounded command operations

See [Bounded command operations](HOST.md#bounded-command-operations).
