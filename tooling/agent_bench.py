"""One-shot OpenRouter coding experiments, with replayable evidence and independent scoring."""
import hashlib
import json
import math
import os
import platform
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import yaml

from tooling.agent_tasks import TASKS, VERSION, inputs, oracle
from tooling.output import emit

API = 'https://openrouter.ai/api/v1'


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
                'You may call the helix tool to inspect embedded library source, format YAML, or test your own examples. '
                'You have bounded tool calls and model turns. Revise independently, then return ONLY your final solve module. '
                'You never see the evaluator cases or reference solution.')
    return [
        {'role': 'system', 'content': 'Write a pure Helix YAML module using the supplied authoritative documentation. '
         'Return ONLY YAML, without Markdown fences or explanation. Export a function named solve taking exactly '
         'one argument. Additional helper definitions and embedded std: imports are allowed. No filesystem imports '
         'or effects. ' + protocol + '\n\n' + documentation},
        {'role': 'user', 'content': public['goal'] + '\nExample: ' + json.dumps(public['example'], ensure_ascii=False)},
    ]


def extract(content):
    """Allow only a single optional transport fence; perform no code repair."""
    match = re.fullmatch(r'\s*```(?:yaml|yml)?\s*\n(.*?)\n```\s*', content, re.DOTALL)
    return (match.group(1) if match else content).strip() + '\n'


def invoke(ctx, directory, values):
    call = {'$call': ['let', {'candidate': {'$call': ['import', 'candidate.yaml']}},
                     {'$call': [{'$ref': 'candidate.solve'}, {'$literal': values}]}]}
    wrapper = directory / 'invoke.yaml'
    wrapper.write_text(json.dumps(call, ensure_ascii=False))
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
    if samples < 1:
        raise ValueError('samples must be positive')
    cases = []
    for name, values in inputs(task):
        _, observation = invoke(ctx, directory, values)
        expected = oracle(task, values)
        passed = observation['exit_code'] == 0 and same(observation['state'].get('root'), expected)
        cases.append({'id': name, 'input': values, 'expected': expected,
                      'passed': passed, 'observation': observation})
    passed = all(case['passed'] for case in cases)
    timings = []
    if passed:
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
    from tooling.agent_cli import TOOL, execute
    mode = getattr(args, 'mode', 'one-shot')
    conversation = [dict(message) for message in prompt]
    responses, calls, api_ms, requests_made = [], 0, 0.0, 0
    response, error = None, None
    for turn in range(getattr(args, 'max_turns', 8) if mode == 'cli' else 1):
        payload = {'model': args.model, 'messages': [dict(message) for message in conversation],
                   'temperature': args.temperature, 'max_tokens': args.max_tokens}
        if mode == 'cli':
            payload['tools'] = [TOOL]
            payload['tool_choice'] = 'required' if turn == 0 else 'auto'
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
            break
        conversation.append({'role': 'assistant', 'content': message.get('content'), 'tool_calls': tool_calls})
        for call in tool_calls:
            calls += 1
            if calls > getattr(args, 'max_tool_calls', 12):
                error = 'CLI tool-call budget exhausted'
                break
            try:
                arguments = json.loads(call.get('function', {}).get('arguments', '{}'))
            except (ValueError, TypeError):
                arguments = None
            if call.get('function', {}).get('name') != 'helix':
                arguments = {'args': ['unsupported-tool']}
            caller = {key: value for key, value in identity.items() if key != 'key'}
            observation = execute(ctx, directory, arguments, caller, calls)
            conversation.append({'role': 'tool', 'tool_call_id': call['id'],
                                 'content': json.dumps(observation, ensure_ascii=False)})
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
            'tool_calls': min(calls, getattr(args, 'max_tool_calls', 12)),
            'generation_ms': api_ms, 'error': error}


def run(root, args, ctx):
    if args.trials < 1 or args.samples < 1 or args.max_tokens < 1 or not 0 <= args.temperature <= 2 or getattr(args, 'max_turns', 8) < 1 or getattr(args, 'max_tool_calls', 12) < 1:
        raise ValueError('trials, samples and max-tokens must be positive; temperature must be in [0,2]')
    selected = args.task or list(TASKS)
    if len(set(selected)) != len(selected) or any(task not in TASKS for task in selected):
        raise ValueError('task IDs must be known and unique; use agent-bench list')
    directory = Path(args.directory).resolve() if args.directory else root / 'runtime' / 'agent-bench' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory.mkdir(parents=True, exist_ok=False)
    docs = context(root)
    (directory / 'context.md').write_text(docs)
    model = None
    key = None
    if not args.dry_run:
        key = api_key(root)
        model = next((m for m in request('/models')['data'] if m['id'] == args.model), None)
        if model is None:
            raise ValueError('exact model ID is absent from OpenRouter catalog; use agent-bench models')
    manifest = {'schema': 'helix.agent-bench.v1', 'task_version': VERSION, 'model': args.model,
                'catalog_model': model, 'mode': getattr(args, 'mode', 'one-shot'),
                'max_turns': getattr(args, 'max_turns', 8), 'max_tool_calls': getattr(args, 'max_tool_calls', 12),
                'trials': args.trials, 'samples': args.samples,
                'temperature': args.temperature, 'max_tokens': args.max_tokens,
                'tasks': selected, 'context_sha256': digest(docs),
                'task_source_sha256': digest((root / 'tooling/agent_tasks.py').read_text()),
                'git_revision': subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=root, capture_output=True, text=True, check=True).stdout.strip(),
                'git_diff_sha256': digest(subprocess.run(['git', 'diff', 'HEAD'], cwd=root, capture_output=True, text=True, check=True).stdout),
                'binary_sha256': hashlib.sha256(ctx.binary.read_bytes()).hexdigest(),
                'environment': {'platform': platform.platform(), 'python': platform.python_version()},
                'timing_contract': 'CLI wall time includes process startup, parsing, linking, execution and serialization; one warmup, n=256. No build or API time. Ratios use a same-run reference; debug build.',
                'dry_run': args.dry_run}
    save(directory / 'manifest.json', manifest)
    save(directory / 'tasks.json', {task: {**TASKS[task], 'cases': [
        {'id': name, 'input': values, 'expected': oracle(task, values)}
        for name, values in inputs(task)]} for task in selected})
    rows = []
    for task in selected:
        reference = directory / task / 'reference'
        reference.mkdir(parents=True)
        (reference / 'candidate.yaml').write_text(TASKS[task]['reference'])
        reference_score = score(ctx, reference, task, args.samples)
        save(reference / 'score.json', reference_score)
        if not reference_score['passed']:
            raise ValueError(f'reference failed for {task}; see {reference / "score.json"}')
        prompt = messages(docs, task, getattr(args, 'mode', 'one-shot'))
        save(directory / task / 'messages.json', prompt)
        if args.dry_run:
            continue
        for trial in range(args.trials):
            attempt = directory / task / f'trial-{trial + 1:03d}'
            attempt.mkdir()
            row = {'task': task, 'trial': trial + 1, 'artifact': str(attempt.relative_to(directory))}
            try:
                generated = generate(ctx, attempt, prompt, args, {
                    'key': key, 'model': args.model, 'benchmark_run': directory.name,
                    'benchmark_task': task, 'benchmark_trial': str(trial + 1),
                })
                response = generated.pop('response')
                row.update(generated)
                if response is not None:
                    save(attempt / 'response.json', response)
                if generated['error']:
                    raise ValueError(generated['error'])
                row['response_model'] = response.get('model')
                row['provider'] = response.get('provider')
                choice = response['choices'][0]
                row['finish_reason'] = choice.get('finish_reason')
                content = choice['message'].get('content')
                if not isinstance(content, str) or not content.strip():
                    raise ValueError('response has no textual program')
                source = extract(content)
                row['source_bytes'] = len(source.encode())
                row['source_sha256'] = digest(source)
                (attempt / 'candidate.yaml').write_text(source)
                row['score'] = score(ctx, attempt, task, args.samples)
                median = row['score'].get('median_cli_wall_ms')
                row['runtime_ratio_to_reference'] = median / reference_score['median_cli_wall_ms'] if median is not None else None
            except (ValueError, KeyError, IndexError, TypeError) as error:
                row['error'] = str(error)
            save(attempt / 'result.json', row)
            rows.append(row)
            save(directory / 'report.json', {'schema': manifest['schema'], 'rows': rows, 'summary': summary(rows)})
            print(f'agent-bench: {task} {trial + 1}/{args.trials}: ' + ('passed' if row.get('score', {}).get('passed') else 'failed'), file=sys.stderr, flush=True)
    return {'directory': str(directory), 'dry_run': args.dry_run, 'summary': summary(rows)}


def replay(root, args, ctx):
    if not args.directory:
        raise ValueError('score requires --directory')
    directory = Path(args.directory).resolve()
    manifest = json.loads((directory / 'manifest.json').read_text())
    if manifest['task_version'] != VERSION or manifest['task_source_sha256'] != digest((root / 'tooling/agent_tasks.py').read_text()):
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
                row['score'] = score(ctx, attempt, task, args.samples)
                median = row['score'].get('median_cli_wall_ms')
                row['runtime_ratio_to_reference'] = median / reference['median_cli_wall_ms'] if median else None
            rows.append(row)
    report = {'schema': 'helix.agent-bench.v1', 'rows': rows, 'summary': summary(rows),
              'replay_binary_sha256': hashlib.sha256(ctx.binary.read_bytes()).hexdigest()}
    save(directory / 'replay.json', report)
    return {'directory': str(directory), 'summary': report['summary']}


def saved_report(args):
    if not args.directory:
        raise ValueError('report requires --directory')
    directory = Path(args.directory).resolve()
    manifest = json.loads((directory / 'manifest.json').read_text())
    report = json.loads((directory / 'report.json').read_text())
    return {'directory': str(directory), 'provenance': {key: manifest.get(key) for key in (
        'schema', 'task_version', 'model', 'trials', 'samples', 'temperature', 'max_tokens',
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
