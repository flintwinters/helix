"""Pinned Ubuntu local/real-SSH facts qualification with independent Docker observations."""
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
import yaml
from tests.suites.docker_nginx import Docker, LABEL
from tests.suites.ssh_host import keygen
from tests.suites.provider_contract import call as c, ref as r, literal as lit, descriptor, context

VERSIONS = json.loads((Path(__file__).parents[1] / 'fixtures/docker_facts/versions.json').read_text())
SELECT = ['package_manager', 'service_manager', 'cpu', 'memory', 'network', 'mounts', 'storage', 'packages', 'services']
VALUE = '/var/lib/helix-facts/value'
OBSERVATION = '/var/lib/helix-facts/observation.json'


def request(custom=True, select=None):
    return {'select': ['cpu'] if select is None else select,
            'custom': [{'name': 'fixture', 'version': '1', 'value_type': r('integer'),
                        'command': {'program': 'cat', 'args': [OBSERVATION]}}] if custom else []}


def configure(target):
    def command(script, *args):
        payload = {'program': 'sh', 'args': ['-c', script, 'facts-fixture', *args]}
        return c('effect', 'command', payload) if target is None else c('effect', 'ssh_command', {**payload, 'host': target})
    spec = descriptor(
        id='ubuntu-facts-fixture', platforms=['ubuntu-26.10'], capabilities=['command' if target is None else 'ssh_command'],
        observe=c('fn', ['args'], c('get', command('cat "$1"', VALUE), 'stdout')),
        apply=c('fn', ['desired', 'steps'], c('if', c('eq', c('get', command('printf %s "$1" > "$2"', r('desired'), VALUE), 'code'), 0),
                                         None, c('raise', 'fixture_failed', 'fixture write failed'))))
    return c('fn', ['snapshot'], {'provider': spec,
        'arguments': c('concat', 'cpu=', c('str', c('facts.value', r('snapshot'), 'cpu'))),
        'context': context(platform='ubuntu-26.10', capabilities=['command' if target is None else 'ssh_command'])})


class Fixture(Docker):
    def __init__(self, h):
        super().__init__(h.root, ssh=True)
        self.h = h
        self.artifacts = h.root / 'runtime/docker-facts' / self.run_id
        self.artifacts.mkdir(parents=True)
        self.image = f'helix-docker-facts:{self.run_id}'
        self.serial_run = 0

    def provision(self):
        assert re.fullmatch(r'ubuntu:26\.10@sha256:[a-f0-9]{64}', VERSIONS['image'])
        self.command('info')
        self.daemon_ready = True
        build = self.artifacts / 'context'
        build.mkdir()
        self.client = self.artifacts / 'client'
        public = keygen(self.client)
        (build / 'authorized_keys').write_text(public + '\n')
        shutil.copy2(self.h.binary, build / 'helix')
        shutil.copy2(self.root / 'tests/fixtures/docker_facts/Dockerfile', build / 'Dockerfile')
        self.command('build', '--platform', VERSIONS['platform'], '--label', f'{LABEL}={self.run_id}',
                     '--build-arg', 'BASE=' + VERSIONS['image'], '-t', self.image, build, timeout=600)
        self.fresh()

    def fresh(self):
        if self.container:
            self.command('rm', '-f', self.container)
        self.container = self.command('run', '-d', '--label', f'{LABEL}={self.run_id}',
                                      '-p', '127.0.0.1::22', self.image).stdout.strip()
        self.port = self.command('port', self.container, '22/tcp').stdout.strip().rsplit(':', 1)[1]
        hostkey = self.exec('cat', '/etc/ssh/ssh_host_ed25519_key.pub').stdout.split()
        self.known = self.artifacts / 'known_hosts'
        self.known.write_text(f'[127.0.0.1]:{self.port} {hostkey[0]} {hostkey[1]}\n')
        self.grants = ['--allow-ssh', f'box,host=127.0.0.1,port={self.port},user=root,known_hosts={self.known},identity={self.client}']
        for _ in range(50):
            probe = subprocess.run(['ssh', '-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                                    '-o', f'UserKnownHostsFile={self.known}', '-i', str(self.client), '-p', self.port,
                                    'root@127.0.0.1', 'true'], capture_output=True, timeout=5)
            if probe.returncode == 0:
                break
            time.sleep(.1)
        else:
            raise AssertionError(probe.stderr)

    def evaluate(self, expression, *, remote, grant=True, error=None):
        self.serial_run += 1
        path = self.artifacts / f'program-{self.serial_run:03d}.yaml'
        path.write_text(json.dumps(c('let', {'facts': c('import', 'std:facts')}, expression)) + '\n')
        if remote:
            process = self.h.helix('perform', *(self.grants if grant else []), str(path))
        else:
            self.command('cp', path, self.container + ':/run/program.yaml')
            flags = [v for program in ('date', 'sh', 'cat', 'ip', 'findmnt', 'lsblk', 'getconf', 'awk', 'dpkg-query')
                     for v in ('--allow-command', program)] if grant else []
            process = self.exec('helix', 'perform', *flags, '/run/program.yaml', check=False)
        path.with_suffix('.state.yaml').write_text(process.stderr)
        state = yaml.safe_load(process.stderr)
        if error:
            assert state['status'] == 'error' and state['error']['kind'] == error, (path, state.get('error'))
            return state
        assert process.returncode == 0 and state['status'] == 'finished', (path, process.returncode, state.get('error', state.get('status')))
        if expression['$call'][0] == 'facts.gather':
            # Identity/platform, selected extras, custom observation and two clocks.
            request_value = expression['$call'][2]
            expected = len(set(['identity', 'platform'] + request_value['select'])) + len(request_value['custom']) + 2
            assert len(state['effects']) == expected, (path, len(state['effects']), expected)
        return state['root']

    def gather(self, remote, *, select=SELECT):
        return self.evaluate(c('facts.gather', 'box' if remote else None, request(select=select)), remote=remote)

    def verify_observations(self, snapshot):
        facts = snapshot['facts']
        assert all(value['status'] == 'known' for name, value in facts.items() if name != 'services'), {k: v for k,v in facts.items() if v['status'] != 'known'}
        assert facts['services']['status'] == 'unsupported', facts['services']
        assert facts['service_manager']['value'] in {'installed-inactive', 'unavailable'}
        os_release = dict(line.split('=', 1) for line in self.exec('cat', '/etc/os-release').stdout.splitlines() if '=' in line)
        assert facts['platform']['value'] == {k: os_release[k].strip('"') for k in ('ID', 'VERSION_ID', 'VERSION_CODENAME')}
        assert facts['cpu']['value'] == int(self.exec('getconf', '_NPROCESSORS_ONLN').stdout)
        mem = int(next(line.split()[1] for line in self.exec('cat', '/proc/meminfo').stdout.splitlines() if line.startswith('MemTotal:'))) * 1024
        assert facts['memory']['value'] == mem
        packages = self.exec('dpkg-query', '-W', '-f=${binary:Package}\t${Version}\t${db:Status-Status}\n').stdout
        expected = [dict(zip(('name', 'version', 'state'), row.split('\t')))
                    for row in packages.splitlines() if row.endswith('\tinstalled')]
        assert facts['packages']['value'] == expected
        # Independent plain-text ip output, rather than trusting the collector's JSON parser.
        addresses = self.exec('ip', '-o', 'address', 'show').stdout.splitlines()
        expected_addresses = {(row.split()[1], row.split()[3]) for row in addresses if row.split()[2] in {'inet', 'inet6'}}
        actual = {(link['ifname'], f"{a['local']}/{a['prefixlen']}") for link in facts['network']['value'] for a in link['addr_info']}
        assert actual == expected_addresses, (actual, expected_addresses)
        mounts = [line.split() for line in self.exec('cat', '/proc/mounts').stdout.splitlines()]
        assert {(v['target'], v['fstype']) for v in facts['mounts']['value']['filesystems']} == {(v[1], v[2]) for v in mounts}
        devices = [line.split() for line in self.exec('lsblk', '--list', '--bytes', '--noheadings', '--output', 'NAME,TYPE,SIZE').stdout.splitlines()]
        assert facts['storage']['value']['blockdevices'] == [dict(name=row[0], type=row[1], size=int(row[2])) for row in devices]
        assert facts['identity']['value']['architecture'] == 'x86_64'
        assert self.exec('cat', VALUE).stdout == 'old'
        assert not any('python' in p['name'] or 'helix-facts' in p['name'] for p in expected)


def verify(h):
    fixture = Fixture(h)
    try:
        fixture.provision()
        initial_packages = fixture.exec('dpkg-query', '-W').stdout
        fixture.exec('mv', '/etc/machine-id', '/run/fixture-machine-id')
        missing_id = fixture.evaluate(c('facts.gather', None, request(custom=False, select=[])), remote=False)
        assert missing_id['facts']['identity']['value']['machine_id']['status'] == 'unknown', missing_id['facts']['identity']
        fixture.exec('mv', '/run/fixture-machine-id', '/etc/machine-id')

        for remote in (False, True):
            target = 'box' if remote else None
            snapshot = fixture.gather(remote)
            fixture.verify_observations(snapshot)
            assert fixture.exec('dpkg-query', '-W').stdout == initial_packages
            cfg = configure(target)
            req = request()
            check = lambda: fixture.evaluate(c('facts.check', target, req, cfg), remote=remote)
            apply = lambda review, **kw: fixture.evaluate(c('facts.apply', target, req, cfg, lit(review)), remote=remote, **kw)
            review = check()
            assert review['review']['status'] == 'changed', review
            # Timestamps differ without being review dependencies.
            fixture.exec('sleep', '1')
            fresh = check()
            assert review == fresh, (review, fresh)
            # Changing an unselected fact has no effect on review authorization.
            fixture.exec('sh', '-c', 'printf unselected > /var/lib/helix-facts/unselected')
            result = apply(review)
            assert result['review']['status'] == 'changed' and result['review']['applied'], result
            assert fixture.exec('cat', VALUE).stdout == review['review']['args']
            repeated = check()
            assert repeated['review']['status'] == 'unchanged'
            assert apply(repeated)['review']['status'] == 'unchanged'
            fixture.exec('sh', '-c', 'printf \'{"version":1,"value":11}\' > "$1"', 'fixture', OBSERVATION)
            apply(repeated, error='facts_stale_review')
            assert fixture.exec('cat', VALUE).stdout == repeated['review']['args']
            fixture.exec('sh', '-c', 'printf \'{"version":1,"value":10}\' > "$1"', 'fixture', OBSERVATION)
            apply(repeated, grant=False, error='facts_stale_review')
            assert fixture.exec('cat', VALUE).stdout == repeated['review']['args']
            fixture.exec('sh', '-c', 'printf old > "$1"', 'fixture', VALUE)
        # Recreating the target must invalidate identity even with the same fixture data.
        review = fixture.evaluate(c('facts.check', 'box', request(), configure('box')), remote=True)
        fixture.fresh()
        fixture.evaluate(c('facts.apply', 'box', request(), configure('box'), lit(review)), remote=True, error='facts_stale_review')
        assert fixture.exec('cat', VALUE).stdout == 'old'
        print(f'docker_facts: local/SSH Ubuntu 26.10 observations and reviewed application passed; {fixture.artifacts.relative_to(h.root)}')
    finally:
        if fixture.daemon_ready:
            fixture.cleanup()
