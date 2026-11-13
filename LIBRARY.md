# Helix Standard Library Reference

Reusable library contracts and testing helpers. For syntax, evaluation and imports,
see the [language reference](LANGUAGE.md). Resource lifecycles and effect grants
live in the [host operations reference](HOST.md).

## Standard libraries

Standard libraries are Helix source embedded in the executable:

| Import | Exports |
|--------|---------|
| `std:set` | `from(xs)`, `contains(xs, value)`, `insert(xs, value)`, `remove(xs, value)`, `union(left, right)`, `intersection(left, right)`, `difference(left, right)`, `is_subset(left, right)`, `is_disjoint(left, right)`, `equals(left, right)`, `symmetric_difference(left, right)` |
| `std:stack` | `push(xs, value)`, `peek(xs, fallback)`, `pop(xs)`; top at the end |
| `std:queue` | `enqueue(xs, value)`, `peek(xs, fallback)`, `dequeue(xs)`; front at index zero |
| `std:map` | `merge(left, right)` (shallow, right wins), `remove(source, key)` (one string key; missing is harmless), `select(source, keys)`, `omit(source, keys)` |
| `std:machine` | Abstract machine classes `host`, `packages`, `files`, `services`, `users`, their combination `machine`, the `port` refinement, and fleet checks `hostnames(fleet)` / `unique_hostnames(fleet)` over realized configs |
| `std:inventory` | `resolve(document, dynamic, selection)` (validated host inventory, explicit connection overrides with provenance, and a reviewable target list) |
| `std:fuzz` | `plan(source)` (ordered damaged-source slice plans) |
| `std:text` | `split(source, separator)` (literal, non-overlapping), `join(parts, separator)` |
| `std:diff` | `compare(old, new)` (shallow configuration changes) |
| `std:validation` | `batch(values, validate)` (indexed validation and transformation reports) |
| `std:dependency` | `plan(tasks)` (stable dependency batches and blocked-task report) |
| `std:provider` | `plan(provider, args, before, context)`, `check(provider, args, context)`, `apply(provider, args, context, reviewed)` ([typed resource lifecycle](HOST.md#resource-provider-contract)) |
| `std:sqlite` | `query(path, sql, params)` (rows as maps), `execute(path, sql, params)` (changed rows), `request(path, sql, params, write)` ([`sqlite` effect](HOST.md#host-effects-and-explicit-grants)) |
| `std:command` | `make(config)`, `plan`, `check`, `apply` ([guarded command resources](HOST.md#guarded-command-resources)) |
| `std:orchestration` | `plan(document)`, `run(document, execute)` (validated resource dependencies and coalesced, deferred change handlers) |
| `std:types` | `from_fields(specs)`, `without_field(t,name)`, `optional_field(spec)`, `optional_fields(t)`, `redeclare(t,parents)` |
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

## List helpers

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
| `golden_plan(observation)` | Decide golden-test failure precedence, blessing and replay from host-captured observations; return `{stage, bless, replay}`. |
| `golden_decision(facts)` | Apply the same decision policy to six boolean comparison facts without transporting captured text. |

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
crashes, timeouts or side effects. `./manage.py test` supplies exact comparison
facts to `golden_decision`, which owns failure precedence, blessing and replay
decisions. `golden_plan` derives the same facts from full observations and delegates
to that policy. Python independently validates decisions against retained raw
observations, supervises interpreter states, runs replay and writes failure
evidence. The same golden runner tests this module; there is no competing test
entrypoint.

`golden_plan` takes a complete observation: `run` and `repeated` maps containing
`returncode`, `stdout` and `stderr`; `expected` is the saved text or null for a
missing file. `bless` is a boolean requesting expectation replacement. `root` is ordinary-root YAML text, or null
when replay is inapplicable (including function/machine inspection graphs).
`replay` is null before replay, then `{returncode, root}` from its result.
Stages are `determinism`, `golden`, `roundtrip`, or `passed`, in that precedence.
The returned `replay` flag requests another observation; `passed` with that flag
is provisional. Text comparisons preserve whitespace and Unicode exactly.
Hosts must pass observations as literal data, supervise every process, and reject
invalid policy responses instead of treating them as a passing test.

`golden_decision` takes a closed record of six boolean fields: `deterministic` compares the complete
run/repeated maps; `matches_expected` compares stdout with expected text;
`bless_requested` carries the caller's request; `replayable` means root is non-null;
`replay_completed` means replay is non-null; and `replay_matches` means a completed
replay returned code zero and the same root text. Absent replay makes the last fact
false. Facts confer no authority: hosts must still veto decisions contradicted by
their original observations. Rejected production policy calls retain those raw
observations in `observation.json` beside the compact program and process output.


## YAML toolkit

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

## JSON and CSV toolkits

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
