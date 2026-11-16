# Agent usability measurements

`./manage.py quality agent-bench` owns model coding experiments.
`./manage.py test --suite agent_bench` verifies the harness offline; paid model
calls are explicit experiments, never part of the default regression suite.

## Protocol v3: edit, execute, submit saved files

The task corpus remains version 2: sum of squares, word counts, grouped totals,
stable deduplication and dependency planning. The default is ten independent
attempts per task. Each exports `solve(input)` from `candidate.yaml`.

Each CLI-assisted attempt has a separate workspace containing authoritative
`LANGUAGE.md` and `LIBRARY.md` files and a read-only `public_test.yaml`. The test
imports `candidate.yaml`, invokes `solve` on the task's disclosed example input,
and asserts the disclosed expected output. It shares the evaluator's invocation
builder, so the public and hidden calling conventions cannot drift.

The model can read documentation progressively, create and edit candidate/helper
files, and run `helix` with argv for `--help`, `std [MODULE]`, `schema`,
`check FILE`, `fmt FILE` or `run FILE`. A `-` file argument accepts source on stdin.
Runtime calls use the existing sandbox with workspace-local and embedded imports
and no host effect grants. Workspace reads and writes cannot escape the attempt
or modify the supplied documentation/public test. Tool feedback is capped at
16,000 characters per stream; full CLI observations remain in the artifacts.

Submission uses a `submit` tool, not another code block in a final answer. It
requires a successful execution of `public_test.yaml` against the exact current
workspace fingerprint. Merely defining `solve` does not qualify. Any file edit
requires another public test. Accepted files are frozen into an evaluation copy;
the implementer applies no repairs and hidden cases remain undisclosed.

Four independent attempts generate concurrently by default (`--jobs`). Each has
up to sixteen model requests and thirty-two total workspace/CLI/submission tool
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
`report.json` updates through generation/scoring/complete phases. The manifest
records protocol/task versions, context delivery, concurrency, sampling, model
catalog, Git/binary/context fingerprints and platform. `report` is read-only and
build-free. `score` replays saved submissions without API calls, respects frozen
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

## Historical protocol-v1 baseline

Protocol v1 used three tasks and five attempts per task. Its task definitions
remain recoverable from the recorded Git history; replay refuses changed task
definitions. The checked-in [one-shot report](baseline-one-shot-v1.json) and
[CLI-assisted report](baseline-cli-v1.json) retain the initial measurements for
`openai/gpt-6-luna`. These are historical GPT-6 results, not measurements of the
requested GPT-6.1 Luna model. Generation used five trials per task. Evaluation was replayed
with five timing samples after the regression jobs finished; both reports select
that replay explicitly. Original API responses, prompts and full scores remain in
the referenced runtime directories. Small-sample differences are observations,
not evidence that CLI access changes the model's underlying success probability.

[Live caller attribution](baseline-cli-attribution-v1.json) records the SQLite
counts for this CLI run. The [failed word-count candidate](evidence/word-counts-cli-trial-002.yaml)
is preserved byte-for-byte: it expected a record containing `input` and returned a
record containing `output`, while the task required a list argument and a count
map result directly. Its self-tests used the same mistaken interface. This is a
concrete interface-understanding failure, despite having execution feedback.

## Recorded 50-attempt protocol-v2 run

The [five-task summary](baseline-cli-5x10-v2.json) records ten CLI-assisted
attempts per task using explicitly approved `openai/gpt-6-luna`, not the unavailable
6.1 default. All 50 attempts produced candidates; 46 passed every oracle case.
There were no API errors. The run used 131 model requests and 81 CLI calls.

[Per-attempt measurements](attempts-cli-5x10-v2.json) preserve all 50 outcomes,
response model IDs, input/output token usage and cost, generation latency,
CLI-call counts, source hashes, correctness counts, five raw timing samples for
successful candidates, and the first failing-case diagnostic. The summary owns
per-task completion intervals and reference runtime ratios. Full API responses,
conversations, programs and six-case observations remain under
`runtime/agent-bench/luna-cli-5x10-v2/`.

[SQLite attribution evidence](baseline-cli-5x10-attribution-v2.json) accounts for
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
