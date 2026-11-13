"""Realization ordering, source blame and immutable class inputs."""
import json

import yaml

from tests.suites.contracts import checked
from tests.suites.source_contracts import location


def verify(h):
    def call(actor, *args):
        return {'$call': [actor, *args]}

    def ref(name):
        return {'$ref': name}

    def predicate(body):
        return call('fn', ['value'], body)

    def declaration(fields, values=None, constraints=None):
        values = {} if values is None else values
        return {'type': 'class', 'name': 'probe', 'parents': [], 'fields': fields,
                'values': values, 'concrete': False,
                'provenance': {key: {'type': 'probe', **({'value': 'probe', 'value_kind': 'explicit'}
                                                       if key in values else {})} for key in fields},
                **({'constraints': constraints} if constraints is not None else {})}

    def inspect_realization(descriptor):
        source = call('let', {'base': descriptor}, {
            'a_result': call('try', call('realize', ref('base')), call('fn', ['error'], ref('error'))),
            'z_original': {key: call('get', ref('base'), key) for key in ('values', 'provenance', 'concrete')},
        })
        result = checked(h, json.dumps(source))
        original = {key: descriptor[key] for key in ('values', 'provenance', 'concrete')}
        assert json.dumps(result['z_original'], sort_keys=True) == json.dumps(original, sort_keys=True), result
        return result['a_result']

    # An executed guard would suspend the public run, even if an implementation
    # later discarded its result. This proves non-execution, not just error order.
    forbidden = predicate(call('effect', 'time', None))
    guarded = {'type': 'refine', 'of': 'integer',
               'check': predicate(call('effect', 'time', None))}
    missing = declaration({'guarded': guarded, 'missing': 'integer',
                           'defaulted': {'type': 'default', 'of': 'integer', 'default': 7}},
                          {'guarded': 1}, [forbidden])
    error = inspect_realization(missing)
    assert error['kind'] == 'abstract' and error['data']['missing'] == [
        {'field': 'missing', 'required_by': 'probe'}], error

    invalid = declaration({'x': 'integer'}, {'x': 'wrong'}, [forbidden])
    error = inspect_realization(invalid)
    assert error['kind'] == 'type' and error['data']['violations'] == [
        {'at': '/x', 'message': 'expected integer, got string'}], error

    constraints = [predicate(False), predicate(7),
                   predicate(call('raise', 'constraint_probe', 'third constraint raised'))]
    error = inspect_realization(declaration({}, constraints=constraints))
    violations = error['data']['violations']
    assert error['kind'] == 'type' and len(violations) == 3, error
    assert violations[:2] == [
        {'at': '/', 'message': 'constraint 0 of `probe` does not hold'},
        {'at': '/', 'message': 'constraint 1 of `probe` failed to run: returned integer, expected boolean'},
    ], error
    assert violations[2]['at'] == '/' and violations[2]['message'].startswith('constraint 2 of `probe` failed to run:'), error
    assert 'third constraint raised' in violations[2]['message'] and error['message'] == violations[0]['message'], error

    descriptor = declaration({'fixed': 'any', 'optional': {'type': 'optional', 'of': 'string'},
                              'defaulted': {'type': 'default', 'of': 'any', 'default': -0.0}}, {'fixed': 1.0})
    realized = inspect_realization(descriptor)
    assert realized['concrete'] is True and set(realized['values']) == {'fixed', 'defaulted'}, realized
    assert type(realized['values']['fixed']) is float and realized['values']['fixed'].hex() == (1.0).hex(), realized
    assert realized['values']['defaulted'].hex() == (-0.0).hex(), realized
    assert realized['provenance'] == {
        'fixed': {'type': 'probe', 'value': 'probe', 'value_kind': 'explicit'},
        'optional': {'type': 'probe'},
        'defaulted': {'type': 'probe', 'value': 'default of probe', 'value_kind': 'default'},
    }, realized
    repeated = checked(h, json.dumps(call('realize', {'$literal': realized})))
    assert repeated == realized and repeated['values']['defaulted'].hex() == (-0.0).hex(), repeated

    # Explicit realization blames its class argument; inherit's missing values
    # belong to the composition call. Policy migration must preserve these spans.
    incomplete = declaration({'needed': 'integer'})
    for expression, path in ((call('realize', incomplete), '/probe/$call/1'),
                             (call('inherit', 'child', [incomplete], {}), '/probe')):
        source = json.dumps({'probe': expression}, indent=2)
        process = h.helix('run', '-', stdin=source)
        assert process.returncode == 1 and h.malformed_state(process.stdout, 1) is None, process
        error = yaml.safe_load(process.stdout)['error']
        assert error['kind'] == 'abstract' and error['path'] == path, error
        assert (error['line'], error['column']) == location(source, path), error
        assert error['data']['missing'] == [{'field': 'needed', 'required_by': 'probe'}], error
