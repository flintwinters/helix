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
    assert oracle('sum-squares', [-2, 0, 2, 3]) == 13
    assert oracle('word-counts', ['a', 'A', '', 'a']) == {'a': 2, 'A': 1, '': 1}
    assert oracle('group-totals', [
        {'category': 'ignored', 'amount': 4, 'active': False},
        {'category': 'zero', 'amount': 4, 'active': True},
        {'category': 'zero', 'amount': -4, 'active': True},
    ]) == {'zero': 0}
    assert list(inputs('word-counts')) == list(inputs('word-counts'))
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
        assert len(saved['rows']) == 2 and len(saved['rows'][0]['score']['cli_wall_ms']) == 1
        with patch.object(bench, 'request', side_effect=AssertionError('replay must be offline')):
            replay = bench.replay(h.root, args, h)
        assert replay['summary']['sum-squares']['pass_rate'] == 0.5
