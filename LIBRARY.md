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
| `std:facts` | Explicit typed Ubuntu observations, pure fact projection and reviewed provider configuration ([facts contracts](#ubuntu-facts-and-reviewed-configuration)) |
| `std:inventory` | `resolve(document, dynamic, selection)` (validated host inventory, explicit connection overrides with provenance, and a reviewable target list) |
| `std:fuzz` | `plan(source)` (ordered damaged-source slice plans) |
| `std:text` | `split(source, separator)` (literal, non-overlapping), `join(parts, separator)` |
| `std:diff` | `compare(old, new)` (shallow configuration changes) |
| `std:validation` | `batch(values, validate)` (indexed validation and transformation reports) |
| `std:dependency` | `plan(tasks)` (stable dependency batches and blocked-task report) |
| `std:provider` | `plan(provider, args, before, context)`, `check(provider, args, context)`, `apply(provider, args, context, reviewed)` ([typed resource lifecycle](HOST.md#resource-provider-contract)) |
| `std:sqlite` | `query(path, sql, params)` (rows as maps), `execute(path, sql, params)` (changed rows), `request(path, sql, params, write)` ([`sqlite` effect](HOST.md#host-effects-and-explicit-grants)) |
| `std:ssh` | `check(host, become, extra)` (observed missing remote tools), `bootstrap(host, become, extra, install)` (declared installer, then re-check), `run(host, become, program, args)` ([SSH effects](HOST.md#ssh-remote-commands)) |
| `std:command` | `make(config)`, `plan`, `check`, `apply` ([guarded command resources](HOST.md#guarded-command-resources)) |
| `std:orchestration` | `plan(document)`, `run(document, execute)` (validated resource dependencies and coalesced, deferred change handlers) |
| `std:types` | `from_fields(specs)`, `without_field(t,name)`, `optional_field(spec)`, `optional_fields(t)`, `redeclare(t,parents)` |
| `std:math` | `number` (numeric type), `abs(n)`, `sign(n)`, `min(a,b)`, `max(a,b)`, `clamp(n,lo,hi)`, `square(n)` |
| `std:list` | `numbers` (numeric list type), `sum(xs)`, `product(xs)`, `reverse(xs)`, `range(n)` (integers from zero while less than `n`, bounded by the iteration limit), `unique(xs)` (first occurrence, structural/numeric equality), `sort_by(xs, before)` (stable; strict ordering predicate), plus [access, traversal and grouping helpers](#list-helpers) |

`std:set`, `std:stack`, and `std:queue` use ordinary lists; `[]` is empty and `len` gives size (normalize sets first). Set operations use structural equality, including numeric equality, and remove duplicates while retaining first encounter order. Union visits left then right; intersection and difference retain left order. `contains` and `is_subset` accept duplicates. Stack and queue `peek` require an explicit fallback, returned only when empty; null and false remain ordinary stored values. Empty `pop` and `dequeue` return `[]`. Every operation is immutable and checks list arguments. List copies and repeated membership scans suit small inputs; set operations can take quadratic time and bounded folds retain the runtime iteration limit.

`std:group` exports `by(xs, key)`: group a list into a map using a function returning a string key. Empty and Unicode keys are valid. Keys use canonical string-key order, and each group's items retain input order. The key function runs once per item; callback failures propagate. List and function arguments are checked even for empty input. Immutable map/list copying can take quadratic time and total allocations; use small inputs within the runtime iteration limit.

`std:ssh.check(host, become, extra)` runs one read-only remote `command -v` pass (under the connection identity) for `sh`'s companions the SSH file effects invoke (`cat`, `mktemp`, `wc`, `chmod`, `mv`, `rm`), `sudo` when `become` is non-null, and the names in `extra` (absolute paths work), returning `{host, ok, missing}`. A check that cannot run is an error (`ssh_prerequisite_check` carrying the remote reply, or the SSH effect's own failure), never an empty gap list. `bootstrap(host, become, extra, install)` does nothing when nothing is missing (`changed: false`, `installer: null`); otherwise it runs the caller-declared `install: {program, args}` (escalated through `become` when set), re-checks, and reports `{host, ok, changed, missing, installer}` where `ok` reflects the fresh observation, not the installer's exit status. No package manager is inferred, installers carry no idempotence or rollback promise, and the caller's `--allow-ssh` grant still governs everything. `run(host, become, program, args)` is the plain `ssh_command` wrapper with an optional escalation user.

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


## Scoped nginx workload

`std:nginx` composes the existing provider and orchestration modules for one
Debian container workload. It exports `resolve`, `observe`, `plan`, `check`,
`apply`, `verify`, `explain`, the composable `configuration` class, and its
input/observation schemas. Every public operation realizes map or class inputs in
a pure region before planning or requesting observation. The class reuses the
input schema, defaults `port` to 8080 and `state` to `present`, and retains class
constraints. `explain` emits versioned ordinary data with resolved `values` and
field `provenance`; the latter records class labels and explicit/default origins,
not full declaration source spans. Optional `ssh_target` selects
a caller-granted SSH alias; otherwise it uses local commands. Both paths share
pure planning, exact fresh-review matching and observed postconditions. This is
a fixture-qualified workload, not a generic service manager. Inputs, entrypoint,
resource semantics and host prerequisites are authoritative in the
[operator contract](tests/fixtures/docker_nginx/README.md).

## Typed Linux host resources

`std:linux` declares Debian `groups`, `users`, `packages`, `directories`, `files` and
`symlinks` as one configuration, with `resolve`, `explain`, `observe`, `plan`, `check`,
`apply`, `verify`, `graph`, the `configuration` class and input/observation schemas. Resources
are identified `kind:key`; prerequisites (a user's group, owners, declared ancestors, explicit
`after`) derive a dependency graph that is validated before effects and is part of the review.
Removal requires explicit `state: absent` and runs in reverse order; omission never deletes. It
reuses `std:provider` and `std:orchestration`, with a helper executable (`std:helper`, shared
framing for local and SSH command effects) supplying mechanisms only. Fixture-qualified; see the
[operator contract](tests/fixtures/docker_linux/README.md).
Resolution rejects conflicting filesystem kinds and invalid dependency graphs, including
absent-resource references. Helpers receive the operation and `-` in argv and request JSON
on stdin; file contents retain exact UTF-8 bytes. Failed application may have changed state
and requires fresh observation; it does not imply rollback or remote process fencing.


## Ubuntu facts and reviewed configuration

`std:facts` supports Ubuntu 26.10 on x86_64 over local commands (`target: null`)
or an existing caller-granted SSH alias (`target: alias`). Collection installs no
collector and needs no remote Python. `selectors` lists the supported builtin
names; `catalog` describes their commands, parsers and value types.

- `gather(target, {select: [...], custom: [...]})` explicitly collects an uncached,
  version 1 ordinary-data snapshot. `identity` and `platform` are always included. Optional selectors are
  `package_manager`, `service_manager`, `network`,
  `mounts`, `storage`, `cpu`, `memory`, `packages` and `services`.
- Each result has `{status, value, diagnostic}`. `known` contains a validated
  value; `unknown`, `inaccessible` and `unsupported` contain null and an explicit
  diagnostic. A missing machine ID is an `unknown` member of the identity value.
  Failed observations never become empty inventories. Installed systemd tools
  and an active systemd manager are different observations; containers without
  the manager report `services` as unsupported.
- `platform` contains `ID`, `VERSION_ID` and `VERSION_CODENAME` parsed from
  OS-release as data. `identity` contains architecture, kernel, hostname,
  machine ID, boot ID and mount namespace. CPU is an online processor count;
  memory is total bytes. Network, mounts and storage use validated native command
  JSON projections. Packages contain installed `{name, version, state}` rows; services contain
  running unit records. Snapshots retain started/finished epoch-second results.
- Custom entries explicitly select `{name, version, value_type, command}`.
  `command` is `{program, args}` and must print JSON `{version: 1, value: ...}`;
  `value_type` validates the value. There are at most 32 custom entries, with
  unique names disjoint from builtin selectors. Declaration versions belong in
  reviews and should change when custom semantics or schemas change. Commands
  are trusted read-only observation code; declarations confer no authority.

`validate_request`, `validate_snapshot`, `value(snapshot, name)`,
`evidence(snapshot)` and `same_evidence(left, right)` are pure. Evidence retains
target, platform/identity, selectors, custom versions and result statuses/values.
Canonical YAML comparison preserves distinguishable numeric representations;
timestamps and diagnostics are excluded. Selected facts are the declared
configuration dependency set; there is no automatic dependency tracing.

`check(target, request, configure)` gathers facts and calls `configure` purely
with their evidence projection. The callback returns
`{provider: descriptor, arguments: value, context: provider_context}` using the
existing [provider lifecycle](HOST.md#resource-provider-contract). Class
constraints and resource or inventory-group selection can use these supplied
values. The result is `{version: 1, evidence, review}`. Configuration receives
no timestamps or diagnostic timing. `plan(snapshot, configure, before)` provides
a pure preview from a supplied validated snapshot and provider observation.

`apply(target, request, configure, review)` always gathers fresh observations,
rejects changed fact evidence, reruns pure configuration, and delegates to
`std:provider.apply` for fresh resource review and postcondition verification.
Unknown identity/platform and loss of their observation authority fail before
mutation. A supplied snapshot is for pure planning, not fresh authorization.
Failures of collection are data; invalid requests, unavailable required values,
unsupported configuration platforms and stale fact evidence raise structured
errors. Effectful assembly helpers remain private lexical bindings; the exported
`apply` entrypoint always performs this fresh collection protocol.

[The read-only example](tests/programs/facts_observe.yaml) uses only existing
command grants. [Ubuntu acceptance](tests/README.md#ubuntu-facts-qualification)
qualifies local and SSH collection and a disposable fixture resource. It does
not extend the Debian-specific `std:linux` provider to Ubuntu, qualify normally
booted systemd services, or migrate this workstation's Ansible configuration.

## HTTP and actors

[HTTP.md](HTTP.md) documents `std:http` response helpers, exact routing and static
files, plus `std:actor` serialized turns and interchangeable persistence callbacks.
Listening and external storage still require caller-owned host grants.

## Source search

`std:source` exposes `parse(text)`, `walk(source)`, `select(source, predicate)`,
`node(source, reference)`, `code_at(source, reference)` and
`enclosing_functions(source, reference)`. Parsing never executes source. A source
record stores normalized `code` once and lists semantic nodes with `reference`,
`parent`, `children`, `kind` and 1-based `location`. References contain `source`
and a `path` of string keys and integer indices. `$literal` is opaque to semantic
walking. Scalar values and declaration annotations remain ordinary data.

`std:search` supplies `functions(record)`, `documentation(record)`,
`keep(record, predicate)`, `project(record, fields)`, `name_matches`,
`signature_matches`, `text_matches`, `regex`, `effect_finding`, `result_type`,
`parameter_type`, `calls(source, name)`, `references(source, name)`,
`match(source, reference, pattern)`, `body_matches(source, pattern)` and
`reachable(edges, start, limit)`. Type relations return a status and basis;
unknown evidence is `unresolved`. Declared types and inferred result types are
separate. Inference is the static checker's bounded fragment, never execution.
`keep` preserves diagnostics and summaries. Projections retain identity,
location and basis. Reachability consumes compact `{from, to}` edges and reports
whether traversal completed within its bound.

Patterns are tagged ordinary data: `{tag: wildcard}`, `{tag: exact, value: ...}`,
`{tag: capture, name: x, of: PATTERN}` (`of` defaults to wildcard),
`{tag: reference, name: x}`, `{tag: call, head: PATTERN, args: [PATTERN, ...]}`,
`{tag: sequence, items: [...]}` and `{tag: mapping, fields: {key: PATTERN}}`.
Mapping patterns match specified fields; `exact: true` also checks field count.
Sequences and call arguments require exact lengths. Captures retain value,
source reference and location; repeated captures require ordinary equality.
Executable call/reference/literal wrappers are distinct from mapping shapes.
Matching establishes syntax, not behavioral equivalence.

`regex_find(pattern, text)` and `std:text.regex` return UTF-8 byte spans and
capture text/spans (unmatched groups are null). The full match is capture zero.
Patterns are limited to 16 KiB, text to 1 MB, compiled automata to 1 MB each,
nesting to 64 and results to 4096 matches. Invalid patterns raise `regex`;
exceeded bounds raise `limit`.

Source-aware pipelines can use `function_candidates(source)` and
`select_functions(source, predicate)` to retain the source's shared code while
adding a `selection` list. `select_bodies(source, pattern)` and
`select_results(source, pattern)` refine that selection in later stages.
`result_expressions(source, reference)` follows explicit `if`, `cond`, `let` and
`type_time` result syntax; other expressions remain expressions to match, without
claims about the values they evaluate to. `result_matches` returns each possible
expression with its syntax basis and capture evidence. Runnable query files live
in `examples/search/`; combine `reusable-functions.yaml` and
`returning-records.yaml` with `exec --stream --file`.

`source_subtype(left, right)` is the bounded checker's non-executing structural
proof relation. It never invokes predicates. `std:search` uses it to distinguish
matches from unresolved type relationships; a failed proof is not a negative
behavioral claim.

`declared_result_type(record, wanted)` selects explicit result contracts;
`result_type` selects inferred evidence. Unannotated parameters and widened `any`
evidence remain unresolved. `relationship(actual, wanted, basis)` centralizes
that policy. Function evidence also supplies a rendered signature for regex
queries. `project` retains type/effect evidence and passes diagnostic/summary
records through intact.

Within reflected nodes, `parent` is null or `{up: N}` (remove `N` typed path
segments); `children` are relative typed paths to append to the node's path.
These relationships preserve source-qualified identity without repeating full
ancestor paths at every edge. Scalar contents are read from the shared `code`.

Reflection stops before its evidence exceeds 8 MB and validates the completed
record against ordinary-data bounds. Expanded typed paths can exhaust those
bounds even for a small source; indexing emits a `limit` diagnostic and an
incomplete summary, while `source_parse` raises `limit`.
