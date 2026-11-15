# Agent usability measurements

`./manage.py quality agent-bench` owns repeated Helix coding experiments.
`./manage.py test --suite agent_bench` verifies the harness offline. Live model
calls are explicit experiments, never part of the default regression suite.

## Protocol v2

The five tasks are positive-integer sum of squares, case-sensitive word counts,
active-record grouped totals, stable deduplication, and deterministic dependency
planning with missing prerequisites and cycles. Each asks for a pure YAML module exporting
`solve(input)`. The model gets the complete current `LANGUAGE.md` and `LIBRARY.md`,
the task specification and one example. The `--mode one-shot` arm gets no tools, oracle cases, reference solution,
execution feedback, repair turns or prior attempts. The default `--mode cli` arm
can inspect Helix and test its own examples, with at most eight model requests
and twelve CLI calls per attempt. Its first request must call the CLI tool; later
requests may call tools or submit the final program. No human repair is applied.
These arms are reported separately; CLI-assisted completion is not one-shot
completion. Documentation is
sent to OpenRouter, so only run with documents approved for that external service.

The requested default model is `openai/gpt-6.1-luna`. Availability is checked
against OpenRouter's current catalog before a run. As of 2026-11-15, this ID is
absent from both the public and authenticated account catalogs; an authenticated
chat-completion request with provider fallback disabled returns HTTP 400:
`openai/gpt-6.1-luna is not a valid model ID`. The official
[Luna-latest page](https://openrouter.ai/~openai/gpt-luna-latest) points to GPT-6
Luna. The default therefore fails explicitly until the requested model is exposed;
no other model is selected automatically. Model IDs are exact; there is no silent fallback. Each task
gets ten independent attempts with identical initial messages and temperature 0.7
by default: 50 attempts over the complete task suite. The
completion limit is 4,096 tokens per model request. One optional YAML transport fence is stripped;
code is never repaired. Provider failures and empty or truncated responses are
retained, with no automatic retry or replacement. The report distinguishes API
observations, evaluated candidates and full successes.

Six deterministic cases per task include empty/singleton input, negatives,
duplicates, zero totals, inactive-only categories, empty strings, case differences
and Unicode. Python computes independent expected results. Exact data kinds
matter: booleans and floats cannot pass as integers. A candidate succeeds only
when all cases pass. Case scores expose partial completion; they do not replace
whole-task success. Candidate imports are confined to its attempt directory and
embedded libraries; no host effect grants are supplied.

Only successful candidates are timed. A 256-element input gets one warmup and
three measured runs; every measured result is checked. Timings are **debug CLI
wall time**, including process startup, parsing, linking, evaluation and output
serialization. Compilation, API calls and Python output decoding are outside the
timer. Raw samples, medians and ratios to a correct same-run reference are saved.
These small workloads measure useful end-to-end latency, not pure evaluator
throughput or asymptotic efficiency. Run on an otherwise idle host for comparisons.

Usage comes directly from OpenRouter's response `usage` object: input, completion,
total tokens, detailed provider fields and reported cost are preserved. Missing
usage/cost stays identifiable through observation counts. Token totals per success
include failed attempts. Generation latency is measured separately from program
runtime. CLI-mode usage sums every model request, including repeated context and tool
feedback. Raw per-request usage remains available. See [OpenRouter chat completions](https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request)
and [usage accounting](https://github.com/OpenRouterTeam/docs/blob/main/cookbook/administration/usage-accounting.mdx).

Ten trials per task still carry sampling uncertainty. Reports include Wilson 95% intervals
for each task's completion rate. Do not infer general agent usability from five
small coding tasks or treat a few milliseconds as an established improvement.
Compare fixed tasks, model, prompt/context hashes, sampling parameters and host
conditions; change one factor at a time. Syntax comparisons require paired context
variants and more trials; v1 establishes the baseline rather than claiming that
YAML tags have improved model performance.

## Commands and evidence

```sh
./manage.py quality agent-bench list
./manage.py quality agent-bench models
./manage.py quality agent-bench run --dry-run
./manage.py quality agent-bench run --mode one-shot --trials 10 --samples 3
./manage.py quality agent-bench run --mode cli --trials 10 --samples 3
./manage.py quality agent-bench run --model openai/gpt-6.1-luna --task word-counts --trials 10
./manage.py quality agent-bench score --directory runtime/agent-bench/RUN
./manage.py quality agent-bench report --directory runtime/agent-bench/RUN
./manage.py quality agent-bench report --replay --directory runtime/agent-bench/RUN
```

Read the key from `OPENROUTER_API_KEY` or `openrouter_api_key` in the environment
or root `.env`. `.env` is ignored by Git. The loader reads only that named value,
never executes shell code and never copies credentials into artifacts or prompts.

Run directories default to `runtime/agent-bench/TIMESTAMP`; `--directory` selects
an explicit new directory. Existing directories are never overwritten by `run`.
Each contains the exact context and messages, public task descriptions, independent
oracle cases, reference sources and scores, candidate sources, raw API responses,
and per-attempt results. `report.json` updates after each attempt, so partial runs
remain inspectable. `manifest.json` records model catalog/pricing, sampling,
context/task/binary hashes, Git revision/diff hash, platform and timing scope.

`report` reads the original results without building, making API calls or rescoring.

`score` replays saved candidates without API calls or charges and writes
`replay.json`; it preserves the original report and API observations. It requires
unchanged task definitions and records the replay binary hash, timing sample count,
host metadata and timestamp. `report --replay` explicitly selects these new
evaluation results while retaining original model usage and generation latency. Replays can test
runtime changes without conflating them with model sampling. Reproduction of the
original runtime still requires its recorded checkout and build.

The offline suite checks correct references, wrong programs, denied imports and
effects, strict oracle equality, stochastic-trial accounting, token/cost totals,
identical independent prompts and API-free replay using mocked completions.

## CLI access and attribution

The model's `helix` tool accepts argv arrays for `--help`, `std [MODULE]`,
`schema`, `fmt -` and `run -`. Source arrives as the tool's stdin field. Execution
uses the existing sandbox with imports confined to the attempt directory and no
host effect grants. Tool feedback is capped at 16,000 characters per stream;
complete stdout, stderr and the executed argv are retained in tool artifacts.
There is no shell or access to credential files through this interface.

Every requested CLI operation, including rejected arguments, is appended through
`tooling/usage_log.py` to the existing SQLite usage log. The runner supplies:

- `caller_role = benchmark-model`
- `model = openai/gpt-6.1-luna` (or the explicitly selected model)
- `agent = openrouter:openai/gpt-6.1-luna`
- `benchmark_run`, `benchmark_task`, and `benchmark_trial`

These values come from the runner, not the model's tool arguments or inherited
implementer identity. Ordinary `manage.py` runtime calls default to
`caller_role = implementer`; historical rows remain unattributed. Automatic oracle
and timing invocations are harness operations and are not labeled as model calls.
A model CLI logging failure stops the experiment rather than silently losing
attribution. `inspect usage` includes caller summaries and identity on recent rows.

```sh
./manage.py inspect usage --sql "SELECT caller_role, model, benchmark_run, benchmark_task, COUNT(*) AS calls FROM calls WHERE caller_role = 'benchmark-model' GROUP BY 1,2,3,4"
```

The offline suite also checks a real SQLite model-attribution row despite an
inherited implementer identity and disabled developer logging, denied CLI
operations, multi-request usage totals, and credential-free saved conversations.

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
