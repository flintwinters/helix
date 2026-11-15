# Agent usability measurements

`./manage.py quality agent-bench` owns repeated one-shot Helix coding experiments.
`./manage.py test --suite agent_bench` verifies the harness offline. Live model
calls are explicit experiments, never part of the default regression suite.

## Protocol v1

The three tasks are positive-integer sum of squares, case-sensitive word counts,
and active-record grouped totals. Each asks for a pure YAML module exporting
`solve(input)`. The model gets the complete current `LANGUAGE.md` and `LIBRARY.md`,
the task specification and one example. It gets no tools, oracle cases, reference
solution, execution feedback, repair turns or prior attempts. Documentation is
sent to OpenRouter, so only run with documents approved for that external service.

The default model is `openai/gpt-6-luna`, verified against OpenRouter's current
catalog before a run. Model IDs are exact; there is no silent fallback. Each task
gets five independent requests with identical messages and temperature 0.7. The
completion limit is 4,096 tokens. One optional YAML transport fence is stripped;
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
runtime. See [OpenRouter chat completions](https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request)
and [usage accounting](https://openrouter.ai/docs/guides/guides/usage-accounting).

Five trials give a noisy initial baseline. Reports include Wilson 95% intervals
for each task's completion rate. Do not infer general agent usability from three
small data tasks or treat a few milliseconds as an established improvement.
Compare fixed tasks, model, prompt/context hashes, sampling parameters and host
conditions; change one factor at a time. Syntax comparisons require paired context
variants and more trials; v1 establishes the baseline rather than claiming that
YAML tags have improved model performance.

## Commands and evidence

```sh
./manage.py quality agent-bench list
./manage.py quality agent-bench models
./manage.py quality agent-bench run --dry-run
./manage.py quality agent-bench run --trials 5 --samples 3
./manage.py quality agent-bench run --model openai/gpt-6-luna --task word-counts --trials 10
./manage.py quality agent-bench score --directory runtime/agent-bench/RUN
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

`score` replays saved candidates without API calls or charges and writes
`replay.json`; it preserves the original report and API observations. It requires
unchanged task definitions and records the replay binary hash. Replays can test
runtime changes without conflating them with model sampling. Reproduction of the
original runtime still requires its recorded checkout and build.

The offline suite checks correct references, wrong programs, denied imports and
effects, strict oracle equality, stochastic-trial accounting, token/cost totals,
identical independent prompts and API-free replay using mocked completions.
