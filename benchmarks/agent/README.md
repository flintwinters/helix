# Agent usability measurements

`./manage.py quality agent-bench` owns model coding experiments.
`./manage.py test --suite agent_bench` verifies the harness offline; paid model
calls are explicit experiments, never part of the default regression suite.
The latest focused v5 follow-ups scored 10/10 each on host-range expansion and
JSON Lines aggregation after targeted prompt and public-example clarifications.
The broader five-task v5 run scored 43/50. These results and the earlier v4,
workspace and final-chat measurements are described below.

## Protocol v5: accept on the first passing public test

Task corpus version 3 has 55 tasks under [tooling/agent_tasks/](../../tooling/agent_tasks/):
the five version-2 tasks plus text processing, record reshaping, CSV/JSON/YAML
formats, infrastructure plans and business reports. They range from one-line
folds to multi-step pipelines, but target common programming work rather than
puzzles: hidden cases scale input to 256 elements, so solutions must stay within
the 10-second and value-budget limits by using Helix's cost model (parameters,
not repeated `let` reads; regex tokenizing; std sorting). Each task's Python oracle
and passing Helix reference live beside it. The recorded corpus-v3 experiments
below cover five of these tasks, not the full corpus. The default is ten independent attempts per task. Each exports `solve(input)` from `candidate.yaml`.

Each CLI-assisted attempt has a separate workspace containing authoritative
`LANGUAGE.md` and `LIBRARY.md` files and a read-only `public_test.yaml`. The test
imports `candidate.yaml`, invokes `solve` on the task's disclosed example input,
and asserts the disclosed expected output. It shares the evaluator's invocation
builder, so the public and hidden calling conventions cannot drift. The driver is
written by `helix fmt` as block-style `!call`/`!ref`/`!literal` source.
Agents can submit any supported YAML layout. After scoring, the harness writes
`candidate.formatted.yaml` for review when formatting succeeds. Submitted
source and the evaluation workspace retain their original bytes.

The model can read documentation progressively, create and edit candidate/helper
files, and run `helix` with argv for `--help`, `help [COMMAND]`,
`docs [DOC [SECTION]]`, `builtins [NAME]`, `std [MODULE[.EXPORT]]`, `schema`,
`check FILE`, `fmt FILE` or `run FILE`; lookups accept `--yaml`. Protocol v4 added
the help/docs/builtins lookups to v3's surface, so the two are not comparable.
A `-` file argument accepts source on stdin.
Runtime calls use the existing sandbox with workspace-local and embedded imports
and no host effect grants. Workspace reads and writes cannot escape the attempt
or modify the supplied documentation/public test. Tool feedback is capped at
16,000 characters per stream; full CLI observations remain in the artifacts.

For a full CLI round, pass `--full-cli`. The harness checks Bubblewrap before
making API calls. Each Helix subprocess receives the actual arguments, a
read-only view of its attempt workspace, private per-call scratch space, and
an isolated network namespace with a cleared environment. Source edits go
through the workspace tool. The manifest records the selected CLI mode.

The first successful `helix run public_test.yaml` ends generation and records
the workspace fingerprint. The harness freezes those files for hidden scoring
before processing any other tool calls. Protocol v4 used a separate submission
step. Merely defining `solve` does not run the public test.

Four independent attempts generate concurrently by default (`--jobs`). Each has
up to sixteen model requests and thirty-two total workspace and CLI tool
calls. API errors remain observations without automatic replacement. Generation
finishes before serial hidden scoring and runtime measurements begin. Only SQLite
log appends are serialized among model workers; the API conversations and CLI
calls themselves remain concurrent.

Six fixed-seed cases per task cover empty/singleton/scaling inputs, Unicode,
duplicates and task-specific boundaries. Independent Python oracles determine
results, with strict data-kind equality. A public-test pass is not hidden-test
success. Only candidates passing every hidden case are timed: one warmup and the
requested sample count on a 256-entry input. Task summaries use medians of
per-candidate CLI medians. Timings include debug CLI startup, parsing, linking,
execution and serialization; compilation, API work and Python decoding are outside
the timer. Correct references run in the same isolated evaluation phase.

Token usage sums every model request, including document reads, tool feedback and
failed attempts. Raw responses preserve detailed usage; missing usage/cost stays
explicit. Generation API latency and full attempt-generation wall time are separate
from program runtime. Requests specify temperature 0.7; the saved model catalog
records provider parameter support. Completion is capped at 4,096 tokens per model
request. Per-task completion rates include Wilson 95% intervals; ten attempts per
task still have broad uncertainty. Keep model, task, protocol and environment
metadata when comparing runs.

`--mode one-shot` remains a separate no-tool baseline: it receives inline language
and library documentation and submits YAML text. It does not use workspace gates.
The requested default model remains `openai/gpt-6.1-luna`; OpenRouter currently
rejects that exact ID, so it fails rather than substituting another model. Runs
with `openai/gpt-6-luna` require that explicit selection. The existing experimental
use of GPT-6 was approved by the user.

## Commands and evidence

```sh
./manage.py quality agent-bench list
./manage.py quality agent-bench run --dry-run
./manage.py quality agent-bench run --mode cli --model openai/gpt-6-luna --trials 10 --jobs 4 --samples 5
./manage.py quality agent-bench score --directory runtime/agent-bench/RUN
./manage.py quality agent-bench report --directory runtime/agent-bench/RUN
./manage.py quality agent-bench report --audit --directory runtime/agent-bench/RUN
./manage.py quality agent-bench report --replay --directory runtime/agent-bench/RUN
```

Read the OpenRouter credential from `OPENROUTER_API_KEY` or `openrouter_api_key`
in the environment or ignored root `.env`. The loader never executes shell code
or copies credentials into model workspaces, prompts or records. Documentation
and requested tool outputs are sent to OpenRouter; the user approved sending the
two documentation files. API contracts follow [chat completions](https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request)
and [usage accounting](https://github.com/OpenRouterTeam/docs/blob/main/cookbook/administration/usage-accounting.mdx).

Run directories default to `runtime/agent-bench/TIMESTAMP`; `--directory` selects
a new explicit path. Each contains context/task snapshots, prompts, responses,
workspace edits, CLI results, conversations, frozen evaluation files and scores.
`report.yaml` updates through generation/scoring/complete phases. The manifest
records protocol/task versions, context delivery, concurrency, sampling, model
catalog, Git/binary/context fingerprints and platform. `report` is read-only and
build-free. `report --audit` recomputes the tested workspace fingerprint and checks
that the archived submission and evaluation copy still match the submitted files;
it makes no model or runtime calls. `score` replays saved submissions without API calls, respects frozen
evaluation directories and writes separate replay measurements.

Every model CLI operation, including rejected argv, is recorded through the
existing SQLite usage log as `caller_role=benchmark-model`, the exact requested
model, `agent=openrouter:MODEL`, and run/task/trial identifiers. These come from
the runner. Normal wrapper calls default to `implementer`; audit replays can use
`benchmark-audit`. Workspace/submission actions have separate per-tool artifacts;
`tool_calls` counts all tools while `cli_calls` matches SQLite CLI rows. Hidden
scoring and timing operations are harness calls and are not labeled as model calls.
Logging failures stop generation rather than silently dropping attribution.

The offline suite covers oracle controls, real SQLite identity, path confinement,
public-test execution versus function declaration, edits after testing, saved-file
submission, multi-turn usage, concurrent generation, isolated scoring and offline
replay. Historical v1/v2 runs below measured final chat-code submissions through
the earlier inline-source tool interface. Their reported failures are real final
program failures; those runs do not measure protocol v3's workspace workflow.

## Corpus-v3 experiments: protocols v4 and v5

Two runs used `openai/gpt-6-luna` on the same five corpus-v3 tasks, with ten
independent attempts per task. The [v4 audited report](baseline-corpus3-v4.yaml)
records 25 hidden-case successes out of 50 attempts; the [v5 audited
report](baseline-corpus3-v5.yaml) records 43 out of 50. All 37 v4 and 48 v5
submitted workspaces passed the fingerprint audit; unsubmitted attempts are not
included in that audit. The reports retain per-task uncertainty and measurements.

| Task | v4 successes | v5 successes |
| --- | ---: | ---: |
| access-log-summary | 9/10 | 10/10 |
| host-range-expand | 6/10 | 7/10 |
| join-orders | 2/10 | 7/10 |
| json-lines-aggregate | 8/10 | 9/10 |
| table-render | 0/10 | 10/10 |

These are not a controlled estimate of the submission change: v4 required an
explicit submission and used a 20-second test timeout; v5 accepted the first
passing public test and used 10 seconds. Their revisions, binaries and context
also differ. Both used eight workers, 32 model requests and 64 tool calls per
attempt as ceilings, and three timing samples. The runs are scoped to these
tasks and model; ten attempts per task leave substantial uncertainty.

Surgical review of failures also drove improvements to the tools agents use:
bounded text operations and discovery, partial matches for broad CLI searches,
guidance on formatting output and indexed lookup, corrected library-source help,
and guidance on avoiding repeated evaluation in callbacks. These changes landed
before the later v4 run; execution and submission behavior changed further before
v5. The v5 join-orders failure analysis also exposed a dotted self-reference
diagnostic bug, fixed after that run; it has not been remeasured here.

Two subsequent v5 experiments clarified the task prompt and public example:
[host-range padding](followup-host-range-v5.yaml) and [JSON Lines non-object
inputs](followup-json-lines-v5.yaml). The former makes the starting endpoint's
minimum padding width observable across a digit boundary; the latter specifies
that valid non-object JSON is invalid input and includes `null` in the example.
Each scored 10/10 and verified all ten submitted workspaces. These are separate samples with changed task definitions,
not replacements for the original scores or proof of a causal improvement.
The host-range run recorded a dirty Git tree; its task-source fingerprint is in
the report. The initial v5 run stopped during generation and is not counted.

Earlier v4 exploration on the same five tasks produced 12/50 successes in the
[32-turn run](exploration-corpus3-32turn-v4.yaml), 16/50 in the [discovery
run](exploration-corpus3-discovery-v4.yaml), and 24/50 in the [second discovery
run](exploration-corpus3-discovery-v2-v4.yaml). These runs include generation
errors and have differing recorded configurations; consult their provenance
before comparing them with each other or with the later run.

Raw conversations, workspaces and hidden-case observations remain in the
corresponding ignored `runtime/agent-bench/` directories named in the reports.
The v4 report uses its original evaluation; its later replay is separate.

## Historical protocol-v1 baseline

Protocol v1 used three tasks and five attempts per task. Its task definitions
remain recoverable from the recorded Git history; replay refuses changed task
definitions. The checked-in [one-shot report](baseline-one-shot-v1.yaml) and
[CLI-assisted report](baseline-cli-v1.yaml) retain the initial measurements for
`openai/gpt-6-luna`. These are historical GPT-6 results, not measurements of the
requested GPT-6.1 Luna model. Generation used five trials per task. Evaluation was replayed
with five timing samples after the regression jobs finished; both reports select
that replay explicitly. Original API responses, prompts and full scores remain in
the referenced runtime directories. Small-sample differences are observations,
not evidence that CLI access changes the model's underlying success probability.

[Live caller attribution](baseline-cli-attribution-v1.yaml) records the SQLite
counts for this CLI run. The [failed word-count candidate](evidence/word-counts-cli-trial-002.yaml)
is preserved byte-for-byte: it expected a record containing `input` and returned a
record containing `output`, while the task required a list argument and a count
map result directly. Its self-tests used the same mistaken interface. This is a
concrete interface-understanding failure, despite having execution feedback.

## Recorded 50-attempt protocol-v2 run

The [five-task summary](baseline-cli-5x10-v2.yaml) records ten CLI-assisted
attempts per task using explicitly approved `openai/gpt-6-luna`, not the unavailable
6.1 default. All 50 attempts produced candidates; 46 passed every oracle case.
There were no API errors. The run used 131 model requests and 81 CLI calls.

[Per-attempt measurements](attempts-cli-5x10-v2.yaml) preserve all 50 outcomes,
response model IDs, input/output token usage and cost, generation latency,
CLI-call counts, source hashes, correctness counts, five raw timing samples for
successful candidates, and the first failing-case diagnostic. The summary owns
per-task completion intervals and reference runtime ratios. Full API responses,
conversations, programs and six-case observations remain under
`runtime/agent-bench/luna-cli-5x10-v2/`.

[SQLite attribution evidence](baseline-cli-5x10-attribution-v2.yaml) accounts for
all 81 CLI calls as `benchmark-model` / `openai/gpt-6-luna`, with ten distinct trial
IDs for every task. Scorer and timing invocations are separate harness operations.

The failed candidates are preserved unchanged:

- [Sum of squares, trial 1](evidence/v2/sum-squares-trial-001.yaml): referenced
  builtin actor `add` as a lexical function value; the observed error was unbound.
- [Sum of squares, trial 7](evidence/v2/sum-squares-trial-007.yaml): invented the
  unsupported `!if` tag.
- [Stable deduplication, trial 8](evidence/v2/stable-unique-trial-008.yaml): tried
  to call the imported library module itself as a function.
- [Dependency planning, trial 2](evidence/v2/dependency-plan-trial-002.yaml):
  returned code with a lexical value-dependency cycle after seven CLI calls.

These are observed first failures, not claims that each program has only one
fault. No candidate was repaired or replaced by the implementer. The measured
runtime is still debug CLI wall time on a 256-entry input, including startup,
loading, execution and serialization; it is not isolated evaluator throughput.

The explicit run command was:

```sh
./manage.py quality agent-bench run --mode cli --model openai/gpt-6-luna --trials 10 --samples 5 --directory runtime/agent-bench/luna-cli-5x10-v2
```


## Recorded workspace run (protocol v3)

The [audited summary](baseline-workspace-5x10-v3.yaml) and
[all 50 per-attempt records](attempts-workspace-5x10-v3.yaml) measure the corrected
workspace workflow with ten attempts on each task, using the approved GPT-6 Luna
model. All 50 submitted saved workspaces passed all six hidden cases. The
summary's submission audit verifies every tested workspace fingerprint, archived
source hash and frozen evaluation copy. It is reproducible with `report --audit`.

There were 52 public-test executions: two initially failed, and the model edited
and retested their code before submission. [Repair traces](workspace-repairs-v3.yaml)
preserve those write/test/submit sequences. The model received no hidden-case
feedback. The [SQLite audit](baseline-workspace-attribution-v3.yaml) accounts for
all 79 actual CLI calls, including 52 public tests, with model identity and ten
distinct trial IDs for every task. All 404 workspace/CLI/submission tool requests
are recorded separately in the local run directory.

Generation used four workers and took 246.9 seconds. After every worker finished,
isolated correctness/timing evaluation took 384.2 seconds. The 350 model requests
consumed 3,271,871 total tokens and reported $0.09931623 in API cost. The small live
pilot is separate and is not counted among these 50 attempts.

This is a new baseline rather than a controlled estimate of a single change:
workspace tools, progressive document access, budgets and submission rules differ
from protocol v2. The earlier 46/50 score described final chat-source submissions,
which could differ from code the model had tested. It must not be treated as the
completion rate of this workspace workflow. Results remain scoped to these five
pure coding tasks and this model; per-task uncertainty is recorded in the summary.

Raw responses, edit logs, workspaces and full hidden-case observations are retained
in `runtime/agent-bench/luna-workspace-5x10-v3/`. The explicit command was:

```sh
./manage.py quality agent-bench run --mode cli --model openai/gpt-6-luna --trials 10 --jobs 4 --samples 5 --directory runtime/agent-bench/luna-workspace-5x10-v3
```
