"""Offline benchmark controls: no credential, API or stochastic correctness dependency."""
import contextlib
import io
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tooling import agent_bench as bench
from tooling.agent_tasks import TASKS, inputs, oracle


def verify(h):
    from tooling.cli import parse
    assert parse(['agent-bench', 'run']).model == 'openai/gpt-6.1-luna'
    assert parse(['agent-bench', 'run']).trials == 10
    assert parse(['agent-bench', 'run']).jobs == 4
    assert len(TASKS) == 55
    # Prompt-visible examples must agree with the hidden oracles they illustrate.
    for name, definition in TASKS.items():
        example = definition['example']
        assert bench.same(oracle(name, example['input']), example['output']), name
    assert oracle('stable-unique', ['b', '', 'b', '雪', '']) == ['b', '', '雪']
    assert oracle('dependency-plan', {'z': [], 'a': ['z'], 'b': []}) == {
        'order': ['b', 'z', 'a'], 'pending': {}, 'missing': [], 'status': 'finished'}
    assert oracle('dependency-plan', {'a': ['b'], 'b': ['a'], 'missing': ['absent', 'absent']}) == {
        'order': [], 'pending': {'a': ['b'], 'b': ['a'], 'missing': ['absent', 'absent']},
        'missing': [{'task': 'missing', 'dependency': 'absent'}] * 2, 'status': 'blocked'}
    assert oracle('sum-squares', [-2, 0, 2, 3]) == 13
    assert oracle('word-counts', ['a', 'A', '', 'a']) == {'a': 2, 'A': 1, '': 1}
    assert oracle('group-totals', [
        {'category': 'ignored', 'amount': 4, 'active': False},
        {'category': 'zero', 'amount': 4, 'active': True},
        {'category': 'zero', 'amount': -4, 'active': True},
    ]) == {'zero': 0}
    assert list(inputs('word-counts')) == list(inputs('word-counts'))
    assert oracle('kv-config-parse', '=v\n k = v \n#x=1\n;y=2') == {'': 'v', ' k ': ' v ', ';y': '2'}
    assert oracle('host-range-expand', ['n[08:10]', 'db']) == ['n08', 'n09', 'n10', 'db']
    assert oracle('ledger-balances', [{'op': 'transfer', 'from': 'a', 'to': 'a', 'amount': 1}]) == {
        'balances': {}, 'rejected': [0]}
    assert oracle('disk-usage-rollup', [{'path': '/f', 'bytes': 3}]) == {'/': 3}
    assert bench.same({'a': [1]}, {'a': [1]})
    assert not bench.same(True, 1) and not bench.same(1.0, 1)
    assert not bench.same({'x': True}, {'x': 1})
    assert bench.extract('```yaml\nsolve: 42\n```') == 'solve: 42\n'
    assert bench.extract('invalid program') == 'invalid program\n'
    assert bench.wilson(0, 5)[0] == 0 and bench.wilson(5, 5)[1] == 1
    with tempfile.TemporaryDirectory(dir=h.root / 'runtime', prefix='agent-bench-contract-') as temporary:
        directory = Path(temporary)
        # Parsing .env must not execute it or expose unrelated fields.
        (directory / '.env').write_text('unrelated=not-a-key\nopenrouter_api_key="fake-test-key"\n')
        with patch.dict(bench.os.environ, {}, clear=True):
            assert bench.api_key(directory) == 'fake-test-key'
        for task in TASKS:
            target = directory / task
            target.mkdir()
            (target / 'candidate.yaml').write_text(TASKS[task]['reference'])
            result = bench.score(h, target, task, 1)
            assert result['passed'] and result['cases_passed'] == 6, (task, result)
        target = directory / 'wrong'
        target.mkdir()
        (target / 'candidate.yaml').write_text('solve: !call [fn, [xs], 0]\n')
        result = bench.score(h, target, 'sum-squares', 1)
        assert not result['passed'] and result['median_cli_wall_ms'] is None, result
        (target / 'candidate.yaml').write_text('solve: !call [fn, [xs], !call [effect, stdout, hello]]\n')
        assert not bench.score(h, target, 'sum-squares', 1)['passed']
        (target / 'candidate.yaml').write_text('solve: !call [import, ../.env]\n')
        assert not bench.score(h, target, 'sum-squares', 1)['passed']
        # A bad trial stays in the denominator; each trial gets exactly one request.
        responses = iter([TASKS['sum-squares']['reference'], 'solve: !call [fn, [xs], 0]'])
        calls = []
        def request(path, payload=None, key=None):
            calls.append((path, payload))
            if path == '/models':
                return {'data': [{'id': 'fake/model'}]}
            return {'model': 'fake/model', 'choices': [{'message': {'content': next(responses)}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 6, 'total_tokens': 16, 'cost': 0.001}}
        args = SimpleNamespace(trials=2, samples=1, max_tokens=100, temperature=0.7,
                               task=['sum-squares'], directory=str(directory / 'run'),
                               model='fake/model', dry_run=False)
        with patch.object(bench, 'request', request), patch.object(bench, 'api_key', return_value='fake'), contextlib.redirect_stderr(io.StringIO()):
            report = bench.run(h.root, args, h)
        assert len(calls) == 3 and calls[1][1]['messages'] == calls[2][1]['messages'], calls
        summary = report['summary']['sum-squares']
        assert summary['total_tokens_per_success'] == 32
        assert summary['pass_rate'] == 0.5 and summary['total_tokens'] == 32, summary
        assert summary['reported_cost_usd'] == 0.002 and summary['attempts'] == 2, summary
        saved = json.loads((Path(args.directory) / 'report.json').read_text())
        assert bench.saved_report(args)['summary']['sum-squares']['total_tokens'] == 32
        assert len(saved['rows']) == 2 and len(saved['rows'][0]['score']['cli_wall_ms']) == 1
        with patch.object(bench, 'request', side_effect=AssertionError('replay must be offline')):
            replay = bench.replay(h.root, args, h)
        assert replay['summary']['sum-squares']['pass_rate'] == 0.5
        args.replay = True
        assert bench.saved_report(args)['evaluation']['samples'] == 1
        assert bench.saved_report(args)['evaluation_source'] == 'replay.json'
        # An unavailable requested version must never become a run of another model.
        missing = SimpleNamespace(**{**vars(args), 'model': 'openai/gpt-6.1-luna',
                                     'directory': str(directory / 'missing-model')})
        def catalog_only(path, payload=None, key=None):
            assert path == '/models', 'unavailable model must not generate a completion'
            return {'data': [{'id': 'openai/gpt-6-luna'}]}
        with patch.object(bench, 'request', catalog_only), patch.object(bench, 'api_key', return_value='fake'):
            try:
                bench.run(h.root, missing, h)
            except ValueError as error:
                assert 'openai/gpt-6.1-luna' in str(error) and 'no substitute' in str(error), error
            else:
                raise AssertionError('missing version silently fell back')
        verify_cli(h, directory)
        verify_concurrent_generation(h, directory)


def verify_cli(h, directory):
    import sqlite3
    from tooling import usage_log
    from tooling.agent_cli import execute

    target = directory / 'cli'
    target.mkdir()
    database = directory / 'usage.sqlite3'
    identity = {'model': 'fake/luna', 'benchmark_run': 'offline-control',
                'benchmark_task': 'sum-squares', 'benchmark_trial': '1'}
    with patch.dict(bench.os.environ, {'HELIX_USAGE_DB': str(database), 'HELIX_AGENT': 'implementer', 'HELIX_USAGE_LOG': '0'}):
        observation = execute(h, target, {'args': ['run', '-'], 'stdin': '!call [add, 20, 22]'}, identity, 1)
    assert observation['exit_code'] == 0, observation
    db = sqlite3.connect(database)
    row = db.execute('SELECT caller_role, model, benchmark_run, benchmark_task, benchmark_trial, agent FROM calls').fetchone()
    db.close()
    assert row == ('benchmark-model', 'fake/luna', 'offline-control', 'sum-squares', '1', 'openrouter:fake/luna'), row
    records = []
    with patch.object(usage_log, 'append', side_effect=lambda root, binary, record: records.append(record)):
        rejected = execute(h, target, {'args': ['perform', '--allow-read', '/']}, identity, 2)
        assert rejected['exit_code'] == 2 and records[-1]['caller_role'] == 'benchmark-model'
        for index, args in enumerate((['help', 'run', '--json'], ['docs', 'language', 'builtins'],
                                      ['builtins', 'get'], ['std', 'list', '--source']), 10):
            looked = execute(h, target, {'args': args}, identity, index)
            assert looked['exit_code'] == 0 and looked['executed_argv'] == args and looked['stdout'], looked
        for index, args in enumerate((['docs', '../secret.md'], ['builtins', 'get', 'map'],
                                      ['help', '--source'], ['docs', 'language', '--evaluate']), 20):
            refused = execute(h, target, {'args': args}, identity, index)
            assert refused['exit_code'] == 2 and refused['executed_argv'] == [], refused
        denied = execute(h, target, {'args': ['run', '-'], 'stdin': '!call [import, ../.env]'}, identity, 3)
        assert denied['exit_code'] == 1 and records[-1]['model'] == 'fake/luna'
        target = directory / 'agent-workspace'
        target.mkdir()
        bench.prepare_workspace(h.root, target, 'sum-squares')
        def response(name, arguments, tokens, cost):
            return {'choices': [{'message': {'content': None, 'tool_calls': [{'id': name, 'type': 'function',
                'function': {'name': name, 'arguments': json.dumps(arguments)}}]}}],
                'usage': {'prompt_tokens': tokens, 'completion_tokens': 2, 'total_tokens': tokens + 2, 'cost': cost}}
        responses = iter([
            response('workspace', {'action': 'write', 'path': 'candidate.yaml', 'content': TASKS['sum-squares']['reference']}, 10, .001),
            response('helix', {'args': ['run', 'public_test.yaml']}, 20, .002),
            response('submit', {}, 5, .001),
        ])
        cli = SimpleNamespace(mode='cli', model='fake/luna', temperature=.7, max_tokens=100, max_turns=4, max_tool_calls=5)
        with patch.object(bench, 'request', side_effect=lambda *args: next(responses)):
            generated = bench.generate(h, target, [{'role': 'user', 'content': 'test'}], cli, {**identity, 'key': 'fake-secret'})
        assert generated['tool_calls'] == 3 and generated['model_requests'] == 3 and generated['error'] is None, generated
        assert generated['public_test_successes'] == 1
        assert generated['submitted_workspace_sha256']
        assert generated['cli_calls'] == 1 and generated['public_test_calls'] == 1, generated
        assert generated['usage']['total_tokens'] == 41 and generated['usage']['cost'] == .004, generated
        assert generated['submitted_sha256'] == bench.digest(TASKS['sum-squares']['reference']), generated
        assert 'fake-secret' not in (target / 'conversation.json').read_text()
        assert records[-1]['caller_role'] == 'benchmark-model'
        import shutil
        shutil.copytree(target / 'workspace', target / 'evaluation')
        shutil.copy2(target / 'workspace/candidate.yaml', target / 'candidate.yaml')
        row = {'task': 'sum-squares', 'trial': 1, 'artifact': target.name,
               'evaluation_directory': 'evaluation', 'source_sha256': generated['submitted_sha256'], **generated}
        audit = bench.submission_audit(directory, [row])
        assert audit['verified'] == 1, audit
        (target / 'evaluation/candidate.yaml').write_text('0')
        audit = bench.submission_audit(directory, [row])
        assert audit['verified'] == 0 and not audit['submissions'][0]['source_matches_submission'], audit
        verify_saved_submission(h, target, identity)


def verify_saved_submission(h, target, identity):
    from tooling.agent_cli import tool
    state = {}
    call = lambda name, payload: tool(h, target, name, payload, identity, 100, state)
    # Defining a function can succeed without executing its faulty body.
    bad = 'solve: !call [fn, [xs], !ref missing]\n'
    assert call('workspace', {'action': 'write', 'path': 'candidate.yaml', 'content': bad}).get('written')
    assert call('helix', {'args': ['run', 'candidate.yaml']})['exit_code'] == 0
    assert 'error' in call('submit', {})
    assert call('helix', {'args': ['run', 'public_test.yaml']})['exit_code'] == 1
    assert 'error' in call('submit', {})
    for path in ('../.env', '/etc/passwd', 'public_test.yaml', 'LANGUAGE.md'):
        assert 'error' in call('workspace', {'action': 'write', 'path': path, 'content': '42'})
    assert 'error' in call('workspace', {'action': 'read', 'path': '../.env'})
    for misrouted in ({'args': ['run', 'x.yaml']}, {'action': 'builtins'}, {'action': 'run', 'path': 'x.yaml'}):
        error = call('workspace', misrouted)['error']
        assert 'call the helix tool' in error, (misrouted, error)
    source = TASKS['sum-squares']['reference']
    call('workspace', {'action': 'write', 'path': 'candidate.yaml', 'content': source})
    assert call('helix', {'args': ['check', 'candidate.yaml']})['exit_code'] == 0
    assert call('helix', {'args': ['run', 'public_test.yaml']})['exit_code'] == 0
    assert call('submit', {})['source_sha256'] == bench.digest(source)
    # Editing after a successful test cannot submit a different, untested artifact.
    call('workspace', {'action': 'write', 'path': 'candidate.yaml', 'content': source + '# edited\n'})
    assert 'error' in call('submit', {})
    assert call('helix', {'args': ['run', 'public_test.yaml']})['exit_code'] == 0
    assert call('submit', {})['submitted']
    assert call('workspace', {'action': 'read', 'path': 'LANGUAGE.md', 'line_count': 3})['total_lines'] > 3


def verify_concurrent_generation(h, directory):
    import threading
    barrier = threading.Barrier(2, timeout=10)
    original_score = bench.score
    def generated(ctx, target, prompt, args, identity):
        barrier.wait()  # A serial scheduler cannot finish either attempt.
        return {'response': {'model': args.model, 'choices': [{'message': {'content': TASKS['sum-squares']['reference']}, 'finish_reason': 'stop'}]},
                'usage': {'prompt_tokens': 10, 'completion_tokens': 2, 'total_tokens': 12},
                'error': None, 'model_requests': 1, 'tool_calls': 0, 'cli_calls': 0,
                'public_test_calls': 0, 'generation_ms': 0}
    def isolated_score(*args):
        assert threading.current_thread() is threading.main_thread(), 'evaluation overlapped worker generation'
        return original_score(*args)
    args = SimpleNamespace(trials=2, samples=1, max_tokens=100, temperature=.7, task=['sum-squares'],
                           directory=str(directory / 'concurrent'), model='fake/model', dry_run=False, jobs=2)
    with patch.object(bench, 'generate', generated), patch.object(bench, 'score', isolated_score), \
         patch.object(bench, 'request', return_value={'data': [{'id': 'fake/model'}]}), \
         patch.object(bench, 'api_key', return_value='fake'), contextlib.redirect_stderr(io.StringIO()):
        report = bench.run(h.root, args, h)
    assert report['summary']['sum-squares']['successes'] == 2, report
    saved = json.loads((Path(args.directory) / 'report.json').read_text())
    assert saved['phase'] == 'complete'
    # A retry regenerates exactly the recorded attempts that never submitted, under their trial numbers.
    failed = Path(args.directory) / 'sum-squares/trial-002/result.json'
    failed.write_text(json.dumps({**json.loads(failed.read_text()), 'error': 'Model turn budget exhausted before a final program'}))
    assert bench.retry_plan(args.directory) == [('sum-squares', 2)]
    assert bench.retry_plan(args.directory, ['sum-squares']) == [('sum-squares', 2)]
    retry = SimpleNamespace(**{**vars(args), 'retry': args.directory, 'directory': str(directory / 'retry'), 'jobs': 1})
    barrier = threading.Barrier(1)  # one retried attempt: generation needs no partner
    with patch.object(bench, 'generate', generated), \
         patch.object(bench, 'request', return_value={'data': [{'id': 'fake/model'}]}), \
         patch.object(bench, 'api_key', return_value='fake'), contextlib.redirect_stderr(io.StringIO()):
        report = bench.run(h.root, retry, h)
    assert [(row['task'], row['trial']) for row in json.loads((Path(retry.directory) / 'report.json').read_text())['rows']] == [('sum-squares', 2)]
    manifest = json.loads((Path(retry.directory) / 'manifest.json').read_text())
    assert manifest['retry_of'] == str(Path(args.directory).resolve()) and manifest['test_timeout_seconds'] == h.timeout
    assert report['summary']['sum-squares']['successes'] == 1, report
