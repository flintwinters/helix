"""Existing nginx planning consumes pure class realization and exposes its provenance."""
import json

import yaml
from tests.suites.contracts import checked
from tooling.source_format import SourceLoader


def verify(h):
    def call(actor, *args):
        return {'$call': [actor, *args]}

    def ref(name):
        return {'$ref': name}

    explicit = {'package_version': 'pinned-test-version', 'server_name': 'helix.local', 'content': 'Hello\n'}
    complete = {**explicit, 'port': 8080, 'state': 'present'}
    fixture = yaml.load((h.root / 'tests/cases/nginx_planning.yaml').read_text(), SourceLoader)
    empty = fixture['$call'][1]['empty']
    bindings = {'nginx': call('import', 'std:nginx'),
                'site': call('inherit', 'site', [ref('nginx.configuration')], explicit)}
    report = checked(h, json.dumps(call('let', bindings, {
        'explained': call('nginx.explain', ref('site')),
        'resolved': call('nginx.resolve', ref('site')),
        'from_map': call('nginx.resolve', explicit),
        'plan_equal': call('eq', call('nginx.plan', ref('site'), empty), call('nginx.plan', complete, empty)),
        'override': call('nginx.resolve', call('inherit', 'override', [ref('site')], {'port': 8081})),
    })))
    assert report['resolved'] == report['from_map'] == complete, report
    assert report['explained']['values'] == complete and report['plan_equal'], report
    provenance = report['explained']['provenance']
    assert provenance['port']['value_kind'] == 'default', provenance
    assert provenance['server_name']['value_kind'] == 'explicit' and provenance['server_name']['value'] == 'site', provenance
    assert report['override'] == {**complete, 'port': 8081}, report

    # Invalid configuration and failed/effectful class constraints fail before
    # nginx can even request its first observation, not merely before mutation.
    cases = [({}, None), ({**complete, 'port': 0}, None),
             (call('constrain', ref('site'), call('fn', ['values'], False)), 'type'),
             (call('constrain', ref('site'), call('fn', ['values'], call('effect', 'time', None))), None)]
    for config, kind in cases:
        result = h.helix('run', '-', stdin=json.dumps(call('let', bindings, call('nginx.observe', config))))
        state = yaml.safe_load(result.stdout)
        assert result.returncode != 0 and state['status'] == 'error', state
        assert 'pending_effect' not in state and not state.get('effects'), state
        if kind:
            assert state['error']['kind'] == kind, state
