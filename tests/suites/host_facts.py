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
        observed = run(f'packages_{n}', c('facts.packages_parse', text))
        assert observed == [dict(name=f'package-{i}', version=f'1:{i}.0-1', state='installed') for i in range(n)]
    run('bad_package', c('facts.packages_parse', 'name,version\n'), error='facts_validation')
    for code, status in [(1, 'inaccessible'), (126, 'unsupported'), (127, 'unsupported'), (69, 'unsupported')]:
        reply = dict(code=code, stdout='', stderr='refused')
        result = run(f'failure_{code}', c('facts.decode', 'packages', lit(reply)))
        assert result == dict(status=status, value=None, diagnostic=reply)
    run('malformed_network', c('facts.decode', 'network', lit(dict(code=0, stdout='[{}]', stderr=''))), error='type')
    decl = {'name': 'fixture', 'version': '1', 'value_type': r('integer'),
            'command': {'program': 'printf', 'args': ['%s', '{"version":1,"value":42}']}}
    custom = c('dict', 'select', [], 'custom', c('list', decl))
    assert run('custom_request', c('get', c('facts.validate_request', custom), 'select')) == []
    result = run('custom_value', c('facts.custom_observe', None, decl), perform=True, flags=('--allow-command', 'printf'))
    assert result == dict(status='known', value=42, diagnostic=None)
    for name, output in [('bad_json', '{'), ('bad_version', '{"version":2,"value":42}'),
                         ('bad_type', '{"version":1,"value":"42"}')]:
        changed = {**decl, 'command': {'program': 'printf', 'args': ['%s', output]}}
        result = run(name, c('facts.custom_observe', None, changed), perform=True, flags=('--allow-command', 'printf'))
        assert result['status'] == 'inaccessible' and result['value'] is None
    excessive = c('dict', 'select', [], 'custom', c('list', *[
        {**decl, 'name': f'fact-{i}'} for i in range(33)]))
    run('excessive', c('facts.validate_request', excessive), error='facts_validation')
    for name, command, flags in [
        ('timeout', {'program': 'sleep', 'args': ['1']}, ('--allow-command', 'sleep', '--command-timeout-ms', '10')),
        ('overflow', {'program': 'printf', 'args': ['%0100d', '1']}, ('--allow-command', 'printf', '--command-output-limit', '16')),
        ('missing_tool', {'program': '/helix-missing-facts-tool', 'args': []}, ('--allow-command', '/helix-missing-facts-tool')),
    ]:
        result = run(name, c('facts.custom_observe', None, {**decl, 'command': command}), perform=True, flags=flags)
        assert result['status'] == 'inaccessible' and result['diagnostic'], (name, result)
    result = run('denied', c('facts.gather', None, lit(request)), perform=True)
    assert all(v['status'] == 'inaccessible' for v in result['facts'].values())
    # Trusted custom observers receive arguments byte-for-byte; no shell interpolation.
    payload = '$(touch runtime/facts-injection); `id`; " ; \\ newline\n'
    decl['value_type'] = r('string')
    decl['command']['args'] = ['%s', json.dumps(dict(version=1, value=payload))]
    assert run('metacharacters', c('facts.custom_observe', None, decl), perform=True,
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
    print(f'host_facts: {count} contracts; artifacts={artifacts.relative_to(h.root)}')


def verify_smoke(h):
    """Explicit, read-only workstation observation; never changes configuration."""
    base = h.root / 'runtime/facts-smoke'
    base.mkdir(parents=True, exist_ok=True)
    source = c('let', {'facts': c('import', 'std:facts')},
               c('facts.gather', None, lit({'select': ['cpu', 'memory', 'packages', 'network', 'mounts'], 'custom': []})))
    path = base / 'observe.yaml'
    path.write_text(json.dumps(source) + '\n')
    flags = [v for program in ('date', 'cat', 'sh', 'ip', 'findmnt', 'getconf', 'awk', 'dpkg-query')
             for v in ('--allow-command', program)]
    result = h.helix('perform', *flags, str(path))
    (base / 'state.yaml').write_text(result.stderr)
    state = yaml.safe_load(result.stderr)
    assert result.returncode == 0 and state['status'] == 'finished', state
    snapshot = state['root']
    assert snapshot['facts']['platform']['value']['ID'] == 'ubuntu', snapshot
    assert snapshot['facts']['platform']['value']['VERSION_ID'] == '26.10', snapshot
    assert snapshot['facts']['identity']['value']['architecture'] == 'x86_64', snapshot
    assert all(result['status'] == 'known' for result in snapshot['facts'].values()), snapshot
    import os
    import subprocess
    assert snapshot['facts']['cpu']['value'] == os.sysconf('SC_NPROCESSORS_ONLN')
    memory = int(next(line.split()[1] for line in Path('/proc/meminfo').read_text().splitlines()
                      if line.startswith('MemTotal:'))) * 1024
    assert snapshot['facts']['memory']['value'] == memory
    packages = subprocess.run(['dpkg-query', '-W', '-f=${binary:Package},${Version},${db:Status-Status}\n'],
                              capture_output=True, text=True, timeout=10, check=True).stdout
    expected = [dict(zip(('name', 'version', 'state'), row.split(',')))
                for row in packages.splitlines() if row.endswith(',installed')]
    assert snapshot['facts']['packages']['value'] == expected
    print('facts_smoke: read-only Ubuntu 26.10 workstation facts match independent observations; runtime/facts-smoke/')
