"""One-shot OpenRouter coding experiments, with replayable evidence and independent scoring."""
import hashlib
import json
import math
import os
import platform
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

from tooling.agent_tasks import TASKS, VERSION, inputs, oracle, source_digest
from tooling.output import emit

API = 'https://openrouter.ai/api/v1'
PROTOCOL_VERSION = 3


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def api_key(root):
    """Read only the named credential; never execute .env or export its other fields."""
    for key in ('OPENROUTER_API_KEY', 'openrouter_api_key'):
        if os.environ.get(key):
            return os.environ[key]
    path = root / '.env'
    for line in path.read_text().splitlines() if path.exists() else ():
        name, separator, value = line.removeprefix('export ').partition('=')
        if separator and name.strip() in ('OPENROUTER_API_KEY', 'openrouter_api_key'):
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if value:
                return value
    raise ValueError('OPENROUTER_API_KEY/openrouter_api_key is missing from environment or .env')


def request(path, payload=None, key=None):
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    req = urllib.request.Request(API + path,
                                 data=json.dumps(payload).encode() if payload is not None else None,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        # Never persist request headers or arbitrary provider error bodies.
        raise ValueError(f'OpenRouter HTTP {error.code}') from None
    except urllib.error.URLError as error:
        raise ValueError(f'OpenRouter connection failed: {type(error.reason).__name__}') from None


def context(root):
    return '\n\n'.join(f'# {name}\n{(root / name).read_text()}'
                       for name in ('LANGUAGE.md', 'LIBRARY.md'))


def messages(documentation, task, mode='one-shot'):
    public = TASKS[task]
    protocol = ('This is one shot: no tools, execution feedback, or repair turn.' if mode == 'one-shot' else
                'Your workspace contains LANGUAGE.md and LIBRARY.md for progressive reading, and a read-only '
                'public_test.yaml that imports candidate.yaml, CALLS solve on the example input and asserts the expected output. '
                'Use workspace tools to read documentation and write candidate.yaml exporting solve(input). '
                'Use helix with args ["run", "public_test.yaml"] to execute that test. Fix problems and rerun as needed. '
                'Running candidate.yaml alone only defines the function; it does not test its body. '
                'Submit the saved files using the submit tool after the public test succeeds. '
                'Do not rewrite code in a final chat answer. You may add helper modules and your own tests. '
                'Hidden oracle cases stay private and run only after submission.')
    return [
        {'role': 'system', 'content': 'Write a pure Helix YAML module using the supplied authoritative documentation. ' +
         ('Return ONLY YAML, without Markdown fences or explanation. ' if mode == 'one-shot' else '') + 'Export a function named solve taking exactly '
         'one argument. Inputs have at most 256 entries. Preserve the output value kinds shown in the example. Additional helper definitions and embedded std: imports are allowed. ' +
         ('No filesystem imports or effects. ' if mode == 'one-shot' else 'Local helper imports must stay inside the workspace. No host effects. ') + protocol + ('\n\n' + documentation if mode == 'one-shot' else '')},
        {'role': 'user', 'content': public['goal'] + '\nExample: ' + json.dumps(public['example'], ensure_ascii=False)},
    ]


def extract(content):
    """Allow only a single optional transport fence; perform no code repair."""
    match = re.fullmatch(r'\s*```(?:yaml|yml)?\s*\n(.*?)\n```\s*', content, re.DOTALL)
    return (match.group(1) if match else content).strip() + '\n'


def invocation(values):
    return {'$call': ['let', {'candidate': {'$call': ['import', 'candidate.yaml']}},
                     {'$call': [{'$ref': 'candidate.solve'}, {'$literal': values}]}]}


def prepare_workspace(root, directory, task):
    workspace = directory / 'workspace'
    workspace.mkdir()
    for name in ('LANGUAGE.md', 'LIBRARY.md'):
        shutil.copy2(root / name, workspace / name)
    example = TASKS[task]['example']
    driver = {'$call': ['assert', {'$call': ['eq', invocation(example['input']),
                                           {'$literal': example['output']}]},
                        'solve(example.input) must equal example.output']}
    (workspace / 'public_test.yaml').write_text(json.dumps(driver, ensure_ascii=False) + '\n')


def invoke(ctx, directory, values):
    wrapper = directory / 'invoke.yaml'
    wrapper.write_text(json.dumps(invocation(values), ensure_ascii=False))
    started = time.perf_counter()
    result = ctx.helix('sandbox', str(directory), str(wrapper))
    elapsed = (time.perf_counter() - started) * 1000
    malformed = ctx.malformed_state(result.stdout, result.returncode)
    state = yaml.safe_load(result.stdout) if not malformed else {}
    return elapsed, {'exit_code': result.returncode, 'state': state,
                     'malformed': malformed, 'stderr': result.stderr}


def same(actual, expected):
    """Exact data kind equality; Python bool must not count as integer 0/1."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(same(actual[k], v) for k, v in expected.items())
    if isinstance(expected, list):
        return len(actual) == len(expected) and all(same(a, b) for a, b in zip(actual, expected))
    return actual == expected


def score(ctx, directory, task, samples):
    if samples < 0:
        raise ValueError('samples cannot be negative')
    cases = []
    for name, values in inputs(task):
        _, observation = invoke(ctx, directory, values)
        expected = oracle(task, values)
        passed = observation['exit_code'] == 0 and same(observation['state'].get('root'), expected)
        cases.append({'id': name, 'input': values, 'expected': expected,
                      'passed': passed, 'observation': observation})
    passed = all(case['passed'] for case in cases)
    timings = []
    if passed and samples:
        values = list(inputs(task))[-1][1]
        for index in range(samples + 1):
            elapsed, observation = invoke(ctx, directory, values)
            if observation['exit_code'] or not same(observation['state'].get('root'), oracle(task, values)):
                return {'passed': False, 'cases': cases, 'cases_passed': sum(c['passed'] for c in cases),
                        'cases_total': len(cases), 'timing_error': observation}
            if index:
                timings.append(elapsed)
    return {'passed': passed, 'cases': cases, 'cases_passed': sum(c['passed'] for c in cases),
            'cases_total': len(cases), 'cli_wall_ms': timings,
            'median_cli_wall_ms': statistics.median(timings) if timings else None}


def wilson(successes, count):
    if not count:
        return None
    z = 1.959963984540054
    proportion = successes / count
    denominator = 1 + z * z / count
    centre = (proportion + z * z / (2 * count)) / denominator
    radius = z * math.sqrt(proportion * (1 - proportion) / count + z * z / (4 * count * count)) / denominator
    return [max(0, centre - radius), min(1, centre + radius)]


def summary(rows):
    groups = {}
    for task in sorted({row['task'] for row in rows}):
        trials = [row for row in rows if row['task'] == task]
        evaluated = [row for row in trials if 'score' in row]
        successes = sum(row['score']['passed'] for row in evaluated)
        usage = [row['usage'] for row in trials if row.get('usage')]
        latencies = [row['generation_ms'] for row in trials if row.get('generation_ms') is not None]
        runtimes = [row['score']['median_cli_wall_ms'] for row in evaluated
                    if row['score'].get('median_cli_wall_ms') is not None]
        ratios = [row['runtime_ratio_to_reference'] for row in trials if row.get('runtime_ratio_to_reference') is not None]
        groups[task] = {
            'model_requests': sum(row.get('model_requests', 1) for row in trials),
            'tool_calls': sum(row.get('tool_calls', 0) for row in trials),
            'cli_calls': sum(row.get('cli_calls', row.get('tool_calls', 0)) for row in trials),
            'public_test_calls': sum(row.get('public_test_calls', 0) for row in trials),
            'public_test_successes': sum(row.get('public_test_successes', 0) for row in trials),
            'attempts': len(trials), 'evaluated': len(evaluated), 'successes': successes,
            'pass_rate': successes / len(trials), 'pass_rate_wilson_95': wilson(successes, len(trials)),
            'mean_case_score': statistics.mean(row['score']['cases_passed'] / row['score']['cases_total']
                                               if 'score' in row else 0 for row in trials),
            'usage_observations': len(usage),
            'prompt_tokens': sum(u.get('prompt_tokens', 0) for u in usage),
            'completion_tokens': sum(u.get('completion_tokens', 0) for u in usage),
            'total_tokens': sum(u.get('total_tokens', 0) for u in usage),
            'mean_total_tokens_per_attempt': sum(u.get('total_tokens', 0) for u in usage) / len(trials) if len(usage) == len(trials) else None,
            'total_tokens_per_success': sum(u.get('total_tokens', 0) for u in usage) / successes if successes and len(usage) == len(trials) else None,
            'reported_cost_usd': sum(u['cost'] for u in usage if isinstance(u.get('cost'), (int, float))),
            'cost_observations': sum(isinstance(u.get('cost'), (int, float)) for u in usage),
            'median_successful_cli_wall_ms': statistics.median(runtimes) if runtimes else None,
            'median_generation_ms': statistics.median(latencies) if latencies else None,
            'median_runtime_ratio_to_reference': statistics.median(ratios) if ratios else None,
        }
    return groups


def generate(ctx, directory, prompt, args, identity):
    from tooling.agent_cli import TOOLS, tool
    mode = getattr(args, 'mode', 'one-shot')
    conversation = [dict(message) for message in prompt]
    responses, calls, api_ms, requests_made = [], 0, 0.0, 0
    response, error = None, None
    state = {}
    cli_calls, public_test_calls, public_test_successes = 0, 0, 0
    for turn in range(getattr(args, 'max_turns', 16) if mode == 'cli' else 1):
        payload = {'model': args.model, 'messages': [dict(message) for message in conversation],
                   'temperature': args.temperature, 'max_tokens': args.max_tokens}
        if mode == 'cli':
            payload['tools'] = TOOLS
            payload['tool_choice'] = 'auto'
        started = time.perf_counter()
        requests_made += 1
        try:
            response = request('/chat/completions', payload, identity['key'])
        except ValueError as failure:
            api_ms += (time.perf_counter() - started) * 1000
            error = str(failure)
            break
        api_ms += (time.perf_counter() - started) * 1000
        responses.append(response)
        save(directory / f'response-{turn + 1:03d}.json', response)
        choices = response.get('choices')
        if not choices:
            error = 'response has no completion choices'
            break
        choice = choices[0]
        message = choice.get('message', {})
        tool_calls = message.get('tool_calls', []) if mode == 'cli' else []
        if not tool_calls:
            conversation.append({'role': 'assistant', 'content': message.get('content')})
            if mode == 'cli':
                conversation.append({'role': 'user', 'content': 'Use the workspace to save candidate.yaml, execute public_test.yaml, and call submit. Final chat text does not submit files.'})
                continue
            break
        conversation.append({'role': 'assistant', 'content': message.get('content'), 'tool_calls': tool_calls})
        for call in tool_calls:
            calls += 1
            if calls > getattr(args, 'max_tool_calls', 32):
                error = 'CLI tool-call budget exhausted'
                break
            try:
                arguments = json.loads(call.get('function', {}).get('arguments', '{}'))
            except (ValueError, TypeError):
                arguments = None
            name = call.get('function', {}).get('name')
            if name == 'helix':
                cli_calls += 1
                if isinstance(arguments, dict) and arguments.get('args') == ['run', 'public_test.yaml']:
                    public_test_calls += 1
            caller = {key: value for key, value in identity.items() if key != 'key'}
            observation = tool(ctx, directory, name, arguments, caller, calls, state)
            if name == 'helix' and isinstance(arguments, dict) and arguments.get('args') == ['run', 'public_test.yaml'] and observation.get('exit_code') == 0:
                public_test_successes += 1
            conversation.append({'role': 'tool', 'tool_call_id': call['id'],
                                 'content': json.dumps(observation, ensure_ascii=False)})
            if observation.get('submitted'):
                state['submitted'] = observation['source_sha256']
                break
        if state.get('submitted'):
            break
        if error:
            break
    else:
        error = 'Model turn budget exhausted before a final program'
    save(directory / 'conversation.json', conversation)
    usages = [r.get('usage') for r in responses]
    usage = None
    if usages and len(usages) == requests_made and all(isinstance(u, dict) and all(isinstance(u.get(field), int) for field in ('prompt_tokens', 'completion_tokens', 'total_tokens')) for u in usages):
        usage = {field: sum(u.get(field, 0) for u in usages)
                 for field in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
        if all(isinstance(u.get('cost'), (int, float)) for u in usages):
            usage['cost'] = sum(u['cost'] for u in usages)
    return {'response': response, 'usage': usage, 'model_requests': requests_made,
            'tool_calls': min(calls, getattr(args, 'max_tool_calls', 32)),
            'generation_ms': api_ms, 'cli_calls': cli_calls, 'public_test_calls': public_test_calls,
            'public_test_successes': public_test_successes,
            'submitted_workspace_sha256': state.get('validated') if state.get('submitted') else None,
            'submitted_sha256': state.get('submitted'), 'error': error}


def generate_attempt(root, args, ctx, directory, docs, task, trial, key):
    attempt = directory / task / f'trial-{trial:03d}'
    attempt.mkdir()
    mode = getattr(args, 'mode', 'one-shot')
    if mode == 'cli':
        prepare_workspace(root, attempt, task)
    row = {'task': task, 'trial': trial, 'artifact': str(attempt.relative_to(directory))}
    started = time.perf_counter()
    try:
        generated = generate(ctx, attempt, messages(docs, task, mode), args, {
            'key': key, 'model': args.model, 'benchmark_run': directory.name,
            'benchmark_task': task, 'benchmark_trial': str(trial),
        })
        response = generated.pop('response')
        row.update(generated)
        if response is not None:
            save(attempt / 'response.json', response)
            row['response_model'] = response.get('model')
            row['provider'] = response.get('provider')
            row['finish_reason'] = response['choices'][0].get('finish_reason')
        if generated['error']:
            raise ValueError(generated['error'])
        if mode == 'cli':
            source = (attempt / 'workspace' / 'candidate.yaml').read_bytes().decode('utf-8')
            if digest(source) != generated['submitted_sha256']:
                raise ValueError('saved submission changed after validation')
            shutil.copytree(attempt / 'workspace', attempt / 'evaluation')
            row['evaluation_directory'] = 'evaluation'
        else:
            content = response['choices'][0]['message'].get('content')
            if not isinstance(content, str) or not content.strip():
                raise ValueError('response has no textual program')
            source = extract(content)
        row['source_bytes'], row['source_sha256'] = len(source.encode()), digest(source)
        (attempt / 'candidate.yaml').write_text(source)
    except (ValueError, KeyError, IndexError, TypeError, OSError) as error:
        row['error'] = str(error)
    row['attempt_generation_wall_ms'] = (time.perf_counter() - started) * 1000
    save(attempt / 'result.json', row)
    return row


def run(root, args, ctx):
    jobs = getattr(args, 'jobs', 1)
    if args.trials < 1 or args.samples < 1 or args.max_tokens < 1 or not 0 <= args.temperature <= 2 or getattr(args, 'max_turns', 16) < 1 or getattr(args, 'max_tool_calls', 32) < 1 or jobs < 1:
        raise ValueError('trial, sample, token, turn, tool and job counts must be positive; temperature must be in [0,2]')
    selected = args.task or list(TASKS)
    if len(set(selected)) != len(selected) or any(task not in TASKS for task in selected):
        raise ValueError('task IDs must be known and unique; use agent-bench list')
    directory = Path(args.directory).resolve() if args.directory else root / 'runtime' / 'agent-bench' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory.mkdir(parents=True, exist_ok=False)
    docs = context(root)
    (directory / 'context.md').write_text(docs)
    model, key = None, None
    if not args.dry_run:
        key = api_key(root)
        model = next((m for m in request('/models')['data'] if m['id'] == args.model), None)
        if model is None:
            raise ValueError(f'{args.model} is absent from OpenRouter catalog; no substitute model was requested')
    manifest = {'schema': 'helix.agent-bench.v1', 'protocol_version': PROTOCOL_VERSION,
                'task_version': VERSION, 'model': args.model, 'catalog_model': model,
                'mode': getattr(args, 'mode', 'one-shot'), 'jobs': jobs,
                'context_delivery': 'workspace-documents' if getattr(args, 'mode', 'one-shot') == 'cli' else 'inline',
                'max_turns': getattr(args, 'max_turns', 16), 'max_tool_calls': getattr(args, 'max_tool_calls', 32),
                'trials': args.trials, 'samples': args.samples, 'temperature': args.temperature,
                'max_tokens': args.max_tokens, 'tasks': selected, 'context_sha256': digest(docs),
                'task_source_sha256': source_digest(),
                'git_revision': subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True, text=True, check=True).stdout.strip(),
                'git_diff_sha256': digest(subprocess.run(['git', 'diff', 'HEAD'], cwd=root, capture_output=True, text=True, check=True).stdout),
                'binary_sha256': hashlib.sha256(ctx.binary.read_bytes()).hexdigest(),
                'environment': {'platform': platform.platform(), 'python': platform.python_version()},
                'timing_contract': 'Generation is concurrent. After all generation stops, CLI correctness and timing run serially; timings include startup, parsing, linking, execution and serialization, with one warmup and n=256. No build or API time; debug build.',
                'dry_run': args.dry_run}
    save(directory / 'manifest.json', manifest)
    save(directory / 'tasks.json', {task: {**TASKS[task], 'cases': [
        {'id': name, 'input': values, 'expected': oracle(task, values)}
        for name, values in inputs(task)]} for task in selected})
    references, rows = {}, []
    for task in selected:
        reference = directory / task / 'reference'
        reference.mkdir(parents=True)
        (reference / 'candidate.yaml').write_text(TASKS[task]['reference'])
        checked = score(ctx, reference, task, 0)
        save(reference / 'score.json', checked)
        if not checked['passed']:
            raise ValueError(f'reference failed for {task}; see {reference / "score.json"}')
        save(directory / task / 'messages.json', messages(docs, task, getattr(args, 'mode', 'one-shot')))
        references[task] = reference
    if args.dry_run:
        return {'directory': str(directory), 'dry_run': True, 'summary': {}}

    generation_started = time.perf_counter()
    generation_wall_ms, evaluation_started = None, None

    def checkpoint(phase):
        rows.sort(key=lambda row: (selected.index(row['task']), row['trial']))
        save(directory / 'report.json', {'schema': manifest['schema'], 'phase': phase,
                                        'execution': {'jobs': jobs,
                                            'generation_wall_ms': generation_wall_ms if generation_wall_ms is not None else (time.perf_counter() - generation_started) * 1000,
                                            'evaluation_wall_ms': (time.perf_counter() - evaluation_started) * 1000 if evaluation_started is not None else 0},
                                        'rows': rows, 'summary': summary(rows)})

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        futures = [pool.submit(generate_attempt, root, args, ctx, directory, docs, task, trial, key)
                   for task in selected for trial in range(1, args.trials + 1)]
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            checkpoint('generation')
            print(f"agent-bench: generated {len(rows)}/{len(futures)}: {row['task']} {row['trial']} "
                  + ('submitted' if not row.get('error') else row['error']), file=sys.stderr, flush=True)
    generation_wall_ms = (time.perf_counter() - generation_started) * 1000
    evaluation_started = time.perf_counter()
    # No API generation or model CLI activity overlaps these measurements.
    for task in selected:
        reference_score = score(ctx, references[task], task, args.samples)
        save(references[task] / 'score.json', reference_score)
        if not reference_score['passed']:
            raise ValueError(f'reference failed for {task} during isolated evaluation')
        for row in [row for row in rows if row['task'] == task]:
            attempt = directory / row['artifact']
            if (attempt / 'candidate.yaml').is_file() and not row.get('error'):
                evaluation = attempt / row.get('evaluation_directory', '.')
                row['score'] = score(ctx, evaluation, task, args.samples)
                median = row['score'].get('median_cli_wall_ms')
                row['runtime_ratio_to_reference'] = median / reference_score['median_cli_wall_ms'] if median is not None else None
            save(attempt / 'result.json', row)
            checkpoint('scoring')
            print(f"agent-bench: scored {task} {row['trial']}/{args.trials}: "
                  + ('passed' if row.get('score', {}).get('passed') else 'failed'), file=sys.stderr, flush=True)
    checkpoint('complete')
    return {'directory': str(directory), 'dry_run': False, 'summary': summary(rows)}


def replay(root, args, ctx):
    if not args.directory:
        raise ValueError('score requires --directory')
    directory = Path(args.directory).resolve()
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest['task_version'] != VERSION or manifest['task_source_sha256'] != source_digest():
        raise ValueError('task version changed; original scoring code is required')
    rows = []
    for task in manifest['tasks']:
        reference = score(ctx, directory / task / 'reference', task, args.samples)
        if not reference['passed']:
            raise ValueError(f'reference failed for {task}')
        for path in sorted((directory / task).glob('trial-*/result.json')):
            row = json.loads(path.read_text())
            attempt = path.parent
            if (attempt / 'candidate.yaml').exists():
                row['score'] = score(ctx, attempt / row.get('evaluation_directory', '.'), task, args.samples)
                median = row['score'].get('median_cli_wall_ms')
                row['runtime_ratio_to_reference'] = median / reference['median_cli_wall_ms'] if median else None
            rows.append(row)
    report = {'schema': 'helix.agent-bench.v1', 'rows': rows, 'summary': summary(rows),
              'evaluation': {'binary_sha256': hashlib.sha256(ctx.binary.read_bytes()).hexdigest(),
                             'samples': args.samples, 'recorded_at': datetime.now(timezone.utc).isoformat(),
                             'environment': {'platform': platform.platform(), 'python': platform.python_version()}}}
    save(directory / 'replay.json', report)
    return {'directory': str(directory), 'summary': report['summary']}


def submission_audit(directory, rows):
    from tooling.agent_cli import fingerprint
    audited = []
    for row in rows:
        if not row.get('submitted_sha256'):
            continue
        item = {'task': row['task'], 'trial': row['trial'],
                'successful_public_tests': row.get('public_test_successes', 0)}
        try:
            attempt = (directory / row['artifact']).resolve()
            evaluation = (attempt / row['evaluation_directory']).resolve()
            if not attempt.is_relative_to(directory.resolve()) or not evaluation.is_relative_to(attempt):
                raise ValueError('artifact directory escapes the saved run')
            workspace = attempt / 'workspace'
            actual = hashlib.sha256((evaluation / 'candidate.yaml').read_bytes()).hexdigest()
            item.update({
                'source_matches_submission': actual == row['source_sha256'] == row['submitted_sha256']
                    == hashlib.sha256((attempt / 'candidate.yaml').read_bytes()).hexdigest(),
                'workspace_matches_tested_fingerprint': fingerprint(attempt) == row['submitted_workspace_sha256'],
                'evaluation_matches_workspace': all(path.read_bytes() == (evaluation / path.relative_to(workspace)).read_bytes()
                    for path in workspace.rglob('*') if path.is_file()),
            })
            item['verified'] = item['successful_public_tests'] > 0 and all(item[key] for key in (
                'source_matches_submission', 'workspace_matches_tested_fingerprint', 'evaluation_matches_workspace'))
        except (OSError, ValueError, KeyError) as error:
            item.update({'verified': False, 'error': str(error)})
        audited.append(item)
    return {'submissions': audited, 'verified': sum(item['verified'] for item in audited), 'total': len(audited)}


def saved_report(args):
    if not args.directory:
        raise ValueError('report requires --directory')
    directory = Path(args.directory).resolve()
    manifest = json.loads((directory / 'manifest.json').read_text())
    filename = 'replay.json' if getattr(args, 'replay', False) else 'report.json'
    report = json.loads((directory / filename).read_text())
    audit = submission_audit(directory, report['rows']) if getattr(args, 'audit', False) else None
    return {'submission_audit': audit, 'evaluation_source': filename, 'evaluation': report.get('evaluation'), 'execution': report.get('execution'), 'directory': str(directory), 'provenance': {key: manifest.get(key) for key in (
        'schema', 'protocol_version', 'context_delivery', 'jobs', 'max_turns', 'max_tool_calls', 'task_version', 'model', 'trials', 'samples', 'temperature', 'max_tokens',
        'context_sha256', 'task_source_sha256', 'git_revision', 'git_diff_sha256', 'binary_sha256',
        'environment', 'timing_contract')}, 'mode': manifest.get('mode', 'one-shot'),
        'summary': summary(report['rows'])}


def dispatch(root, args, ctx=None):
    try:
        if args.action == 'list':
            report = {'task_version': VERSION, 'tasks': {k: {f: v[f] for f in ('goal', 'example')} for k, v in TASKS.items()}}
        elif args.action == 'models':
            report = {'models': [{'id': m['id'], 'pricing': m.get('pricing')} for m in request('/models')['data']]}
        elif args.action == 'report':
            report = saved_report(args)
        elif args.action == 'run':
            report = run(root, args, ctx)
        else:
            report = replay(root, args, ctx)
        emit({'command': 'agent-bench', 'status': 'ok', **report})
        return 0
    except (ValueError, OSError, yaml.YAMLError) as error:
        emit({'command': 'agent-bench', 'status': 'error', 'error': str(error)})
        return 2
