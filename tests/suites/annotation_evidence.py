"""Successful annotation inspection preserves source and ordinary value semantics."""
import json

import yaml


def verify(h):
    def call(actor, *args):
        return {'$call': [actor, *args]}

    def evidence_assert(condition, label, source, result, state):
        if condition:
            return
        from tooling.test_reports import failure
        rows = state.get('annotations', [])
        summary = {'status': state.get('status'), 'stdout_characters': len(result.stdout),
                   'stderr_characters': len(result.stderr), 'annotation_count': len(rows),
                   'objects_present': 'objects' in state,
                   'annotations_truncated': state.get('annotations_truncated', False)}
        raise AssertionError(failure(h.root, h.root / 'tests/suites/annotation_evidence.py',
            label, json.dumps(summary), 'run', result, source=json.dumps(source)))

    number = {'$ref': 'number'}
    identity = call('fn', [['n', number]], {'$ref': 'n'}, number)
    source = call('let', {'number': 'integer', 'identity': identity},
                  call('expect', call('identity', 7), number))
    result = h.helix('run', '-', stdin=json.dumps(source))
    state = yaml.safe_load(result.stdout)
    assert result.returncode == 0 and state['root'] == 7, result
    rows = state['annotations']
    assert [row['kind'] for row in rows] == ['parameter', 'result', 'expect'], rows
    assert all(row['value'] == 7 and row['type'] == 'integer' and
               row['declaration']['code'] == number for row in rows), rows
    assert [row['declaration']['location']['path'] for row in rows] == [
        ['$call', 1, 'identity', '$call', 1, 0, 1],
        ['$call', 1, 'identity', '$call', 3], ['$call', 2, '$call', 2]], rows
    assert all('target' in row for row in rows) and 'annotations_truncated' not in state

    untyped = call('let', {'identity': call('fn', ['n'], {'$ref': 'n'})}, call('identity', 7))
    result = h.helix('run', '-', stdin=json.dumps(untyped))
    assert result.returncode == 0 and yaml.safe_load(result.stdout) == {'status': 'finished', 'root': 7}, result
    rejected = h.helix('run', '-', stdin=json.dumps(call('expect', 'wrong', 'integer')))
    assert rejected.returncode == 1 and 'annotations' not in yaml.safe_load(rejected.stdout), rejected

    # Evidence never retains or inspects live objects' captured graphs. These
    # values close over substantial data, but produce only shallow marked rows.
    for kind in ('function', 'vm'):
        function = call('fn', [], {'$ref': 'capture'})
        value = {'$ref': 'callback'} if kind == 'function' else call('vm', {'$ref': 'callback'})
        source = call('let', {'capture': 'large-capture-' * 20000, 'callback': function},
                      call('type', call('expect', value, kind)))
        result = h.helix('run', '-', stdin=json.dumps(source))
        state = yaml.safe_load(result.stdout)
        evidence_assert(result.returncode == 0 and state.get('root') == kind and 'objects' not in state,
                        f'{kind}-projection', source, result, state)
        rows = state.get('annotations', [])
        row = rows[0] if rows else {}
        projected = row.get('value', {})
        evidence_assert(row.get('runtime_values_omitted') is True and isinstance(projected, dict)
                        and projected.get('kind') == kind and set(projected) ==
                        ({'kind', 'location'} if kind == 'function' else {'kind', 'status'})
                        and len(json.dumps(row)) < 1000 and 'large-capture-' not in json.dumps(row),
                        f'{kind}-bounded-row', source, result, state)
    ordinary = {'kind': 'vm', 'status': 'ready'}
    result = h.helix('run', '-', stdin=json.dumps(call('expect', ordinary, 'map')))
    row = yaml.safe_load(result.stdout)['annotations'][0]
    assert result.returncode == 0 and row['value'] == ordinary and 'runtime_values_omitted' not in row, result

    descriptor = {'type': 'record', 'fields': {'n': {'type': 'default', 'of': 'integer', 'default': 7}}}
    for _ in range(2):
        result = h.helix('run', '-', stdin=json.dumps({'$literal': descriptor}))
        state = yaml.safe_load(result.stdout)
        assert result.returncode == 0 and state == {'status': 'finished', 'root': descriptor}, result
        descriptor = state['root']

    # Two successful annotations per distinct argument exceed retained evidence,
    # while source/result data remain below the ordinary per-value size bound.
    values = [f'{index}:' + 'x' * 3000 for index in range(220)]
    source = call('map', call('fn', [['text', 'string']], {'$ref': 'text'}, 'string'), values)
    result = h.helix('run', '-', stdin=json.dumps(source))
    state = yaml.safe_load(result.stdout)
    evidence_assert(result.returncode == 0 and state.get('root') == values,
                    'bounded-history-result', source, result, state)
    evidence_assert(state.get('annotations_truncated') is True and 0 < len(state.get('annotations', [])) < 440,
                    'bounded-history-size', source, result, state)
