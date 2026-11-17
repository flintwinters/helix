"""Facts parser, observation and dependency contracts with independent Python oracles."""
import json
import tempfile
from pathlib import Path
import yaml
from tests.suites.provider_contract import call as c, ref as r, literal as lit


def verify(h):
    base = h.root / 'runtime/host-facts'
    base.mkdir(parents=True, exist_ok=True)
    artifacts = Path(tempfile.mkdtemp(dir=base, prefix='run-'))
    count = 0

    def run(name, expression, *, flags=(), error=None, perform=False):
        nonlocal count
        count += 1
        source = c('let', {'facts': c('import', 'std:facts')}, expression)
        path = artifacts / f'{name}.yaml'
        path.write_text(json.dumps(source) + '\n')
        process = h.helix('perform' if perform else 'run', *flags, str(path))
        output = process.stderr if perform else process.stdout
        path.with_suffix('.state.yaml').write_text(output)
        assert h.malformed_state(output, process.returncode) is None, (name, process)
        state = yaml.safe_load(output)
        if error:
            assert state['status'] == 'error' and state['error']['kind'] == error, (name, state.get('error', state.get('status')))
            return state
        assert process.returncode == 0 and state['status'] == 'finished', (name, state.get('error', state.get('status')))
        return state['root']

    def custom_result(declaration):
        snapshot = c('facts.gather', None, {'select': [], 'custom': c('list', declaration)})
        return c('get', c('get', snapshot, 'custom'), 'fixture')

    request = {'select': ['cpu'], 'custom': []}
    assert run('request', c('facts.validate_request', lit(request))) == request
    for name, value in [('unknown', {'select': ['bogus'], 'custom': []}),
                        ('duplicate', {'select': ['cpu', 'cpu'], 'custom': []})]:
        run(name, c('facts.gather', None, lit(value)), error='facts_validation', perform=True)
    run('shape', c('facts.validate_request', lit({'select': 'cpu', 'custom': []})), error='type')
    hostile = 'ID=ubuntu\nVERSION_ID="26.10"\nVERSION_CODENAME=stonking\nIGNORED=$(touch /never)\n'
    assert run('os_release', c('facts.os_release', hostile)) == {
        'ID': 'ubuntu', 'VERSION_ID': '26.10', 'VERSION_CODENAME': 'stonking'}
    run('missing_os', c('facts.os_release', 'ID=ubuntu\n'), error='type')
    run('bad_identity', c('facts.identity_parse', 'a\nb\n'), error='facts_validation')
    for n in (0, 1, 4000):
        text = ''.join(f'package-{i},1:{i}.0-1,installed\n' for i in range(n))
        observed = run(f'packages_{n}', c('get', c('facts.decode', 'packages', lit(dict(code=0, stdout=text, stderr=''))), 'value'))
        assert observed == [dict(name=f'package-{i}', version=f'1:{i}.0-1', state='installed') for i in range(n)]
    run('bad_package', c('facts.packages_parse', 'name,version\n'), error='facts_validation')
    for code, status in [(1, 'inaccessible'), (126, 'unsupported'), (127, 'unsupported'), (69, 'unsupported')]:
        reply = dict(code=code, stdout='', stderr='refused')
        result = run(f'failure_{code}', c('facts.decode', 'packages', lit(reply)))
        assert result == dict(status=status, value=None, diagnostic=reply)
    run('malformed_network', c('facts.decode', 'network', lit(dict(code=0, stdout='[{}]', stderr=''))), error='type')
    native_network = [{'ifindex': 1, 'ifname': 'lo', 'flags': ['LOOPBACK'],
                       'addr_info': [{'family': 'inet', 'local': '127.0.0.1', 'prefixlen': 8, 'valid_life_time': 99}]}]
    native_reply = dict(code=0, stdout=json.dumps(native_network), stderr='')
    assert run('network_projection', c('facts.decode', 'network', lit(native_reply)))['value'] == [
        dict(ifindex=1, ifname='lo', addr_info=[dict(family='inet', local='127.0.0.1', prefixlen=8)])]
    service_reply = dict(code=0, stdout=json.dumps([dict(unit='fixture.service', active='active', sub='running', load='loaded')]), stderr='')
    assert run('services_projection', c('facts.decode', 'services', lit(service_reply)))['value'] == [
        dict(unit='fixture.service', active='active', sub='running')]
    decl = {'name': 'fixture', 'version': '1', 'value_type': r('integer'),
            'command': {'program': 'printf', 'args': ['%s', '{"version":1,"value":42}']}}
    custom = c('dict', 'select', [], 'custom', c('list', decl))
    assert run('custom_request', c('get', c('facts.validate_request', custom), 'select')) == []
    result = run('custom_value', custom_result(decl), perform=True, flags=('--allow-command', 'printf'))
    assert result == dict(status='known', value=42, diagnostic=None)
    for name, output in [('bad_json', '{'), ('bad_version', '{"version":2,"value":42}'),
                         ('bad_type', '{"version":1,"value":"42"}')]:
        changed = {**decl, 'command': {'program': 'printf', 'args': ['%s', output]}}
        result = run(name, custom_result(changed), perform=True, flags=('--allow-command', 'printf'))
        assert result['status'] == 'inaccessible' and result['value'] is None
    excessive = c('dict', 'select', [], 'custom', c('list', *[
        {**decl, 'name': f'fact-{i}'} for i in range(33)]))
    run('excessive', c('facts.validate_request', excessive), error='facts_validation')
    for name, command, flags in [
        ('timeout', {'program': 'sleep', 'args': ['1']}, ('--allow-command', 'sleep', '--command-timeout-ms', '10')),
        ('overflow', {'program': 'printf', 'args': ['%0100d', '1']}, ('--allow-command', 'printf', '--command-output-limit', '16')),
        ('missing_tool', {'program': '/helix-missing-facts-tool', 'args': []}, ('--allow-command', '/helix-missing-facts-tool')),
    ]:
        result = run(name, custom_result({**decl, 'command': command}), perform=True, flags=flags)
        assert result['status'] == 'inaccessible' and result['diagnostic'], (name, result)
    result = run('denied', c('facts.gather', None, lit(request)), perform=True)
    assert all(v['status'] == 'inaccessible' for v in result['facts'].values())
    # Trusted custom observers receive arguments byte-for-byte; no shell interpolation.
    payload = '$(touch runtime/facts-injection); `id`; " ; \\ newline\n'
    decl['value_type'] = r('string')
    decl['command']['args'] = ['%s', json.dumps(dict(version=1, value=payload))]
    assert run('metacharacters', custom_result(decl), perform=True,
               flags=('--allow-command', 'printf'))['value'] == payload
    assert not (h.root / 'runtime/facts-injection').exists()
    from tests.suites.provider_contract import descriptor, context
    def known(value):
        return dict(status='known', value=value, diagnostic=None)
    snapshot = dict(version=1, target=None, selectors=['identity', 'platform', 'package_manager', 'service_manager', 'cpu'],
                    custom_versions=[], custom={}, started=known(1), finished=known(2),
                    facts={
                        'identity': known(dict(architecture='x86_64', kernel='test', hostname='fixture',
                                               machine_id=known('machine'), boot_id='boot', namespace='mnt:[1]')),
                        'platform': known(dict(ID='ubuntu', VERSION_ID='26.10', VERSION_CODENAME='stonking')),
                        'package_manager': known(['/usr/bin/dpkg-query']), 'service_manager': known('installed-inactive'),
                        'cpu': known(4)})
    cfg = c('fn', ['selected'], {'provider': descriptor(), 'arguments': 'new', 'context': context()})
    plan = run('pure_plan', c('type_time', c('facts.plan', lit(snapshot), cfg, 'old')))
    assert plan['review']['status'] == 'changed' and plan['evidence']['facts']['cpu']['value'] == 4
    changed = {**snapshot, 'started': known(999), 'finished': known(1000)}
    changed['facts'] = {**snapshot['facts'], 'cpu': {**known(4), 'diagnostic': {'timing': 100}}}
    assert run('timestamp_equality', c('facts.same_evidence', lit(snapshot), lit(changed))) is True
    identity = snapshot['facts']['identity']['value']
    changed_identity = {**identity, 'machine_id': {**identity['machine_id'], 'diagnostic': {'timing': 100}}}
    changed = {**snapshot, 'facts': {**snapshot['facts'], 'identity': known(changed_identity)}}
    assert run('nested_diagnostic_equality', c('facts.same_evidence', lit(snapshot), lit(changed))) is True
    for name, value in [('changed_cpu', 5)]:
        changed = {**snapshot, 'facts': {**snapshot['facts'], 'cpu': known(value)}}
        assert run(name, c('facts.same_evidence', lit(snapshot), lit(changed))) is False
    changed = {**snapshot, 'facts': {**snapshot['facts'], 'cpu': known('four')}}
    run('snapshot_schema', c('facts.validate_snapshot', lit(changed)), error='type')
    numeric = {**snapshot, 'custom_versions': [{'name': 'number', 'version': '1'}], 'custom': {'number': known(4)}}
    numeric_float = {**numeric, 'custom': {'number': known(4.0)}}
    assert run('numeric_representation', c('facts.same_evidence', lit(numeric), lit(numeric_float))) is False
    changed = {**snapshot, 'facts': {**snapshot['facts'], 'identity': known({**snapshot['facts']['identity']['value'], 'boot_id': 'other'})}}
    assert run('changed_identity', c('facts.same_evidence', lit(snapshot), lit(changed))) is False
    bad_cfg = c('fn', ['selected'], c('effect', 'stdout', 'forbidden'))
    run('pure_configuration', c('facts.plan', lit(snapshot), bad_cfg, 'old'), error='purity')
    unsupported = {**snapshot, 'facts': {**snapshot['facts'], 'platform': known(dict(ID='debian', VERSION_ID='13', VERSION_CODENAME='trixie'))}}
    run('unsupported_platform', c('facts.plan', lit(unsupported), cfg, 'old'), error='facts_validation')
    # The callback sees only its declared evidence; clock/diagnostic data cannot affect configuration.
    cfg = c('fn', ['selected'], c('if', c('has', r('selected'), 'started'),
                                c('raise', 'unexpected_timestamp', 'timestamp leaked'),
                                {'provider': descriptor(), 'arguments': 'new', 'context': context()}))
    assert run('configuration_projection', c('facts.plan', lit(snapshot), cfg, 'old'))['review']['status'] == 'changed'
    # Existing class constraints and inventory group policy consume ordinary supplied facts.
    def class_config(minimum):
        machine = c('class', 'facts-config', c('record', 'cores', r('integer')),
                    {'cores': c('facts.value', r('selected'), 'cpu')})
        realized = c('realize', c('constrain', machine, c('fn', ['candidate'],
                                                      c('gt', c('get', r('candidate'), 'cores'), minimum))))
        return c('fn', ['selected'], {'provider': descriptor(),
            'arguments': c('str', c('get', c('get', realized, 'values'), 'cores')), 'context': context()})
    assert run('class_configuration', c('facts.plan', lit(snapshot), class_config(2), 'old'))['review']['args'] == '4'
    run('class_constraint', c('facts.plan', lit(snapshot), class_config(8), 'old'), error='type')
    inventory_doc = {'version': 1, 'hosts': [{'id': 'fixture', 'connection': {'transport': 'local'}}],
                     'groups': {'capable': c('if', c('gt', c('facts.value', r('selected'), 'cpu'), 2), ['fixture'], [])},
                     'overrides': []}
    inventory = c('call', c('get', c('import', 'std:inventory'), 'resolve'), inventory_doc, [],
                  lit({'all': False, 'hosts': [], 'groups': ['capable'], 'exclude_hosts': [], 'exclude_groups': []}))
    cfg = c('fn', ['selected'], {'provider': descriptor(),
                                'arguments': c('str', c('len', c('get', inventory, 'targets'))), 'context': context()})
    assert run('inventory_configuration', c('facts.plan', lit(snapshot), cfg, 'old'))['review']['args'] == '1'
    run('private_apply_assembly', c('facts.apply_snapshot', None, None, None), error='unbound')
    print(f'host_facts: {count} contracts; artifacts={artifacts.relative_to(h.root)}')
