"""Declaration, predicate and argument blame through the ordinary evaluator."""
import json

import yaml

from tests.suites.source_contracts import location


def verify(h):
    def call(actor, *args):
        return {'$call': [actor, *args]}

    def ref(name):
        return {'$ref': name}

    def failure(program):
        source = json.dumps(program, indent=2)
        result = h.helix('run', '-', stdin=source)
        state = yaml.safe_load(result.stdout)
        assert result.returncode == 1 and state['status'] == 'error', (result, state)
        return source, state['error']

    source, error = failure({'f': call('fn', [['left', 'string'], ['right', 'integer']], None),
                             'bad': call('f', 1, False)})
    assert error['kind'] == 'type' and error['path'] == '/bad/$call/1', error
    assert (error['line'], error['column']) == location(source, error['path']), error
    found = error['data']['violations']
    assert [row['argument'] for row in found] == ['left', 'right'], found
    for index, row in enumerate(found, 1):
        at = row['argument_location']
        expected_path = f'/bad/$call/{index}'
        assert at['path'] == expected_path, row
        assert (at['line'], at['column']) == location(source, expected_path), row
    assert error['calls'][0]['path'] == '/bad', error

    _, error = failure(call('map', call('fn', [['item', 'string']], ref('item')), [1]))
    assert error['path'] == '/$call/1', error
    assert error['data']['violations'][0]['argument_location']['path'] == '/$call/1', error

    _, error = failure({'bad_type': {'type': 'list'}, 'checked': call('expect', [], ref('bad_type'))})
    assert error['kind'] == 'type' and error['path'] == '/bad_type', error

    for body, original_kind in ((7, None), (call('raise', 'rule_bug', 'predicate exploded'), 'rule_bug')):
        _, error = failure({'rule': call('fn', ['value'], body),
                            'checked': call('expect', 1, call('refine', 'integer', ref('rule')))})
        assert error['kind'] == 'predicate' and error['path'] == '/rule', error
        assert error['data']['violations'][0]['rule'] == 'predicate_error', error
        if original_kind is not None:
            assert error['cause']['kind'] == original_kind, error

    function = call('fn', [], 1, {'type': 'list'})
    _, error = failure(call(function))
    assert error['kind'] == 'type' and error['path'] == '/$call/0/$call/3', error

    typed = call('fn', [['value', 'integer']], ref('value'))
    for expression, expected_path in (
            (call('call', typed, 'bad'), '/$call/2'),
            (call(call('if', True, typed, typed), 'bad'), '/$call/1')):
        source, error = failure(expression)
        assert error['path'] == expected_path, error
        assert (error['line'], error['column']) == location(source, expected_path), error

    # Whole-program evaluation visits independent fields once, including nested
    # failures, but does not execute the body of an uncalled function.
    program = {'first': call('expect', 1, 'string'),
               'nested': {'second': call('expect', False, 'integer'),
                          'valid': call('expect', 3, 'integer')},
               'uncalled': call('fn', [], 'bad', 'integer')}
    source = json.dumps(program, indent=2)
    process = h.helix('check', '--evaluate', '-', stdin=source)
    state = yaml.safe_load(process.stdout)
    assert process.returncode == 1 and state['status'] == 'error', state
    assert sorted(error['path'] for error in state['errors']) == [
        '/first/$call/1', '/nested/second/$call/1'], state
    definition = json.dumps(program['uncalled'])
    evaluated = h.helix('check', '--evaluate', '-', stdin=definition)
    assert evaluated.returncode == 0 and yaml.safe_load(evaluated.stdout)['status'] == 'finished', evaluated
    analyzed = h.helix('check', '-', stdin=definition)
    assert analyzed.returncode == 1 and yaml.safe_load(analyzed.stdout)['diagnostics'], analyzed

    # Predicate blame is shared, but independent checked values remain distinct
    # evidence, including language-equal values with different numeric tags.
    for values in ((1, 2), (1, 1.0)):
        program = {'rule': call('fn', ['value'], 7),
                   'guarded': call('refine', 'any', ref('rule')),
                   'first': call('expect', values[0], ref('guarded')),
                   'second': call('expect', values[1], ref('guarded'))}
        process = h.helix('check', '--evaluate', '-', stdin=json.dumps(program))
        state = yaml.safe_load(process.stdout)
        assert process.returncode == 1 and state['status'] == 'error', state
        errors = state['errors']
        assert len(errors) == 2, state
        assert all(error['kind'] == 'predicate' and error['path'] == '/rule'
                   for error in errors), errors
        actuals = [error['data']['violations'][0]['actual'] for error in errors]
        assert [(type(value), value) for value in actuals] == [
            (type(value), value) for value in values], errors

    # Traversing several aliases of one failed dependency still reports the
    # same evidence once; collection is not a count of reference traversals.
    shared = {'rule': call('fn', ['value'], 7),
              'guarded': call('refine', 'any', ref('rule')),
              'failed': call('expect', 1, ref('guarded')),
              'first': ref('failed'), 'second': {'again': ref('failed')}}
    process = h.helix('check', '--evaluate', '-', stdin=json.dumps(shared))
    state = yaml.safe_load(process.stdout)
    assert process.returncode == 1 and state['status'] == 'error', state
    assert len(state['errors']) == 1, state
    assert state['errors'][0]['data']['violations'][0]['actual'] == 1, state

    # A signature inspection failure is an ordinary membership violation.
    # A later broad function alternative can still accept that function;
    # this differs from a broken refinement predicate, which is fatal.
    signature = call('function_of', ['integer'], 'integer')
    accepted = call('union', signature, 'function')
    program = call('let', {'candidate': call('fn', [['value', 'not_a_type']], ref('value'))}, {
        'signature_only': call('is', ref('candidate'), signature),
        'signature_rule': call('get', call('get', call('violations', ref('candidate'), signature), 0), 'rule'),
        'union_member': call('is', ref('candidate'), accepted),
        'union_violations': call('violations', ref('candidate'), accepted),
        'union_expect': call('is', call('expect', ref('candidate'), accepted), 'function')})
    process = h.helix('run', '-', stdin=json.dumps(program))
    state = yaml.safe_load(process.stdout)
    assert process.returncode == 0 and state['status'] == 'finished', state
    assert state['root'] == {'signature_only': False, 'signature_rule': 'signature_error',
                             'union_member': True, 'union_violations': [], 'union_expect': True}, state

    # A later thrown predicate must not become the cause of an earlier
    # nonboolean predicate failure. Ordinary false rejections are not failures.
    for first, last, blamed, cause in ((7, call('raise', 'later', 'later failure'), 'first_rule', None),
                                      (False, call('raise', 'later', 'later failure'), 'last_rule', 'later'),
                                      (call('raise', 'first', 'first failure'), 7, 'first_rule', 'first')):
        _, error = failure({'first_rule': call('fn', ['value'], first),
                            'last_rule': call('fn', ['value'], last),
                            'checked': call('expect', {'a': 1, 'b': 2}, call('record',
                                'a', call('refine', 'integer', ref('first_rule')),
                                'b', call('refine', 'integer', ref('last_rule'))))})
        assert error['kind'] == 'predicate' and error['path'] == f'/{blamed}', error
        if cause is None:
            assert 'cause' not in error, error
        else:
            assert error['cause']['kind'] == cause, error

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory(dir=h.root / 'runtime', prefix='declarative-diagnostics-') as directory:
        module = Path(directory) / 'module.yaml'
        module.write_text(json.dumps({'accept': typed,
            'rule': call('fn', ['value'], call('raise', 'imported_rule', 'broken module rule')),
            'guarded': call('refine', 'integer', ref('rule'))}))
        bindings = {'module': call('import', str(module))}
        _, error = failure(call('let', bindings, call('module.accept', 'bad')))
        assert error['path'] == '/$call/2/$call/1' and 'file' not in error, error
        assert error['data']['violations'][0]['argument_location']['reference']['source'] == '<root>', error
        _, error = failure(call('let', bindings, call('expect', 1, ref('module.guarded'))))
        assert error['kind'] == 'predicate' and error['path'] == '/rule', error
        assert error['file'].endswith('/module.yaml') and error['cause']['kind'] == 'imported_rule', error
