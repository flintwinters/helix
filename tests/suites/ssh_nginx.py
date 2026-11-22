"""Opt-in controller-to-disposable-host qualification of the existing nginx policy."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time

import yaml

from tests.suites import docker_nginx as local
from tests.suites.ssh_host import keygen


class Remote(local.Docker):
    def __init__(self, h):
        super().__init__(h.root, ssh=True)
        self.h = h
        self.grants = []
        self.controller_serial = 0

    def connect(self):
        client = self.artifacts / 'client-key'
        public = keygen(client)
        self.exec('mkdir', '-p', '/run/sshd', '/run/helix-ssh')
        self.exec('ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', '/run/helix-ssh/host')
        self.put('/run/helix-ssh/authorized_keys', public + '\n')
        self.exec('chown', '-R', '0:0', '/run/helix-ssh')
        self.exec('chmod', '700', '/run/helix-ssh')
        self.exec('chmod', '600', '/run/helix-ssh/authorized_keys')
        # Permit key authentication to the disposable container account; passwords
        # and keyboard-interactive authentication remain disabled in its sshd.
        self.exec('usermod', '-p', 'x', 'root')
        self.put('/run/helix-ssh/config', '\n'.join([
            'Port 2222', 'ListenAddress 0.0.0.0', 'HostKey /run/helix-ssh/host',
            'PidFile /run/helix-ssh/sshd.pid', 'AuthorizedKeysFile /run/helix-ssh/authorized_keys',
            'PermitRootLogin prohibit-password', 'PasswordAuthentication no',
            'KbdInteractiveAuthentication no', 'PubkeyAuthentication yes', 'UsePAM no',
            'StrictModes yes', 'LogLevel VERBOSE', 'AllowUsers root', 'AllowTcpForwarding no', 'X11Forwarding no', '']))
        self.command('exec', '-d', self.container, '/usr/sbin/sshd', '-D', '-E', '/run/helix-ssh/server.log', '-f', '/run/helix-ssh/config')
        self.wait_path('/run/helix-ssh/sshd.pid')
        port = int(self.command('port', self.container, '2222/tcp').stdout.strip().rsplit(':', 1)[1])
        host_key = self.exec('cat', '/run/helix-ssh/host.pub').stdout.split()
        known = self.artifacts / 'known_hosts'
        known.write_text(f'[127.0.0.1]:{port} {host_key[0]} {host_key[1]}\n')
        self.grants = ['--allow-ssh', f'fixture,host=127.0.0.1,port={port},user=root,known_hosts={known},identity={client}']
        self.private_key = client
        self.known_hosts = known

    def args(self, *, grant=True, checkpoint=None, resume=None, timeout_ms=120000):
        args = [str(self.h.binary), 'perform', '--allow-stdin', '--allow-stdout',
                '--command-timeout-ms', str(timeout_ms), *(self.grants if grant else [])]
        if resume:
            return [*args, '--resume-checkpoint', str(resume)]
        if checkpoint:
            args += ['--checkpoint', str(checkpoint)]
        return [*args, str(self.root / 'tests/programs/docker_nginx/operate.yaml')]

    def controller(self, mode, inputs, review=None, *, grant=True, timeout_ms=120000):
        if 'ssh_target' not in inputs:
            return super().controller(mode, inputs, review, grant=grant, timeout_ms=timeout_ms)
        request = {'mode': mode, 'config': inputs}
        if review is not None:
            request['before' if mode == 'plan' else 'review'] = review
        self.controller_serial += 1
        prefix = self.artifacts / f'ssh-controller-{self.controller_serial:04d}'
        prefix.with_suffix('.input.json').write_text(json.dumps(request))
        result = subprocess.run(self.args(grant=grant, timeout_ms=timeout_ms), input=json.dumps(request), text=True,
                                capture_output=True, cwd=self.root, timeout=timeout_ms / 1000 + 60)
        prefix.with_suffix('.stdout').write_text(result.stdout)
        prefix.with_suffix('.stderr').write_text(result.stderr)
        return local.controller_report(result)


def recovery(d, inputs):
    """Lose an SSH reply after mutation, reconcile it, and continue the saved plan."""
    changed = {**inputs, 'server_name': 'resumed.helix.test'}
    review = d.controller('check', changed)
    d.fault({'files-install': 'after-mutation'})
    checkpoint = d.artifacts / 'interrupted.json'
    request = json.dumps({'mode': 'apply', 'config': changed, 'review': review})
    stdout = (d.artifacts / 'interrupted.stdout').open('w')
    stderr = (d.artifacts / 'interrupted.stderr').open('w')
    process = subprocess.Popen(d.args(checkpoint=checkpoint), cwd=d.root, stdin=subprocess.PIPE,
                               stdout=stdout, stderr=stderr, text=True, start_new_session=True)
    child_groups = []
    try:
        process.stdin.write(request)
        process.stdin.close()
        try:
            d.wait_fault()
        except AssertionError as error:
            raise AssertionError(f'{error}; controller={process.poll()}; ' +
                                 (d.artifacts / 'interrupted.stderr').read_text()) from error
        paused = json.loads(d.exec('cat', '/run/helix-nginx/fault-reached.json').stdout)
        assert paused['pid'] > 1
        assert process.poll() is None, (d.artifacts / 'interrupted.stderr').read_text()
        observed = d.probe()
        for path in local.FILES[:2]:
            assert observed['files'][path]['content'] == local.expected_files(changed)[path], observed
        assert observed['http']['activation'] != f"1:{changed['server_name']}:{changed['port']}"
        doc = json.loads(checkpoint.read_text())
        assert doc['phase'] == 'in_flight'
        snapshot = doc['snapshot']
        machine = snapshot['objects'][snapshot['machine']]['value']
        pending = snapshot['objects'][machine['evaluator']]['value']['pending_effect']['request']
        assert pending['operation'] == 'ssh_command'
        # Children are owned OpenSSH process groups created by this controller.
        children = Path(f'/proc/{process.pid}/task/{process.pid}/children').read_text().split()
        child_groups = [os.getpgid(int(pid)) for pid in children]
        os.kill(process.pid, signal.SIGKILL)
        for group in child_groups:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=10)
        before_actions = d.actions()
        refused = subprocess.run(d.args(resume=checkpoint), text=True, capture_output=True, cwd=d.root, timeout=30)
        assert refused.returncode != 0 and 'uncertain' in refused.stderr, refused
        assert d.actions() == before_actions
        # Stop the fixture's deliberately paused command, independently verify its
        # installed configuration, and acknowledge that observed mutation. No SSH
        # request is replayed by reconciliation.
        d.exec('python3', '-c', 'import os,signal,sys\ntry: os.kill(int(sys.argv[1]),signal.SIGKILL)\nexcept ProcessLookupError: pass', str(paused['pid']))
        d.fault()
        payload = {'host': 'fixture', 'program': local.HOST,
                   'args': ['files-install', json.dumps({'files': {
                       path: {'content': local.expected_files(changed)[path], 'uid': 0, 'gid': 0, 'mode': 420}
                       for path in local.FILES[:2]}, 'validate': True}, separators=(',', ':'), sort_keys=True)]}
        # Match the exact retained argument spelling rather than reconstructing JSON
        # whitespace; the independent probe above establishes the postcondition.
        def ordinary(value):
            if 'map' in value:
                return {k: ordinary(v) for k, v in value['map'].items()}
            if 'list' in value:
                return [ordinary(v) for v in value['list']]
            for key in ('string', 'integer', 'boolean'):
                if key in value:
                    return value[key]
            if 'null' in value:
                return None
            raise AssertionError(value)
        actual_payload = ordinary(pending['payload'])
        assert actual_payload['host'] == payload['host'] and actual_payload['program'] == local.HOST
        assert actual_payload['args'][0] == 'files-install'
        assert json.loads(actual_payload['args'][1]) == json.loads(payload['args'][1])
        outcome = d.artifacts / 'observed-outcome.json'
        outcome.write_text(json.dumps({'request': {'id': pending['id'], 'operation': 'ssh_command', 'payload': actual_payload},
            'outcome': {'ok': {'code': 0, 'stdout': '{}\n', 'stderr': ''}}}))
        reconciled = d.h.helix('reconcile', str(checkpoint), str(outcome))
        assert reconciled.returncode == 0, reconciled
        resumed = subprocess.run(d.args(resume=checkpoint), text=True, capture_output=True, cwd=d.root, timeout=180)
        (d.artifacts / 'resumed.stdout').write_text(resumed.stdout)
        (d.artifacts / 'resumed.stderr').write_text(resumed.stderr)
        assert local.controller_report(resumed)['status'] == 'changed'
        d.converged(changed)
        assert [row['operation'] for row in d.actions()[len(before_actions):]] == ['service-reload']
        assert str(d.private_key) not in checkpoint.read_text() and str(d.known_hosts) not in checkpoint.read_text()
        d.apply(changed, expected='unchanged')
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        for group in child_groups:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=10)
        stdout.close(); stderr.close()
        d.fault()


def verify(h):
    d = Remote(h)
    result = {'version': 1, 'suite': 'ssh_nginx', 'status': 'failed', 'run_id': d.run_id,
              'versions': local.VERSIONS, 'scenarios': [],
              'controller_sha256': hashlib.sha256(h.binary.read_bytes()).hexdigest(),
              'source_sha256': {name: hashlib.sha256((h.root / name).read_bytes()).hexdigest()
                  for name in ('std/nginx.yaml', 'tests/suites/ssh_nginx.py', 'tests/suites/docker_nginx.py',
                               'tests/fixtures/docker_nginx/Dockerfile', 'tests/fixtures/docker_nginx/host.py',
                               'tests/fixtures/docker_nginx/supervisor.py')}}
    previous_termination = signal.getsignal(signal.SIGTERM)
    def interrupted(signum, frame):
        raise KeyboardInterrupt('SSH nginx suite cancelled')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        d.progress(suite="ssh_nginx", phase="provision")
        d.provision()
        # Keep the same local acceptance baseline before changing its transport.
        d.progress(suite="ssh_nginx", phase="local-baseline")
        local.baseline(d)
        d.fresh()
        d.connect()
        d.progress(suite="ssh_nginx", phase="remote-convergence")
        inputs = local.desired(ssh_target='fixture')
        before, actions = d.probe(), d.actions()
        assert d.controller('resolve', inputs) == inputs
        review = d.controller('check', inputs)
        assert review['status'] == 'changed' and d.probe() == before and d.actions() == actions, review
        denied = d.controller('apply', inputs, review, grant=False)
        assert denied['status'] == 'failed' and 'capability' in json.dumps(denied)
        assert d.probe() == before and d.actions() == actions
        d.apply(inputs, review=review)
        d.converged(inputs)
        for _ in range(2):
            before, actions = d.probe(), d.actions()
            d.apply(inputs, expected='unchanged')
            assert d.probe() == before and d.actions() == actions
        result['scenarios'].append('local-and-ssh-convergence-noop-authority')
        review = d.controller('check', inputs)
        d.put(local.FILES[2], 'independent drift\n')
        drifted, actions = d.probe(), d.actions()
        stale = d.controller('apply', inputs, review)
        assert stale['status'] == 'failed' and d.probe() == drifted and d.actions() == actions
        d.apply(inputs)
        d.converged(inputs)
        result['scenarios'].append('stale-review-and-drift-repair')
        known = d.known_hosts.read_text()
        d.known_hosts.write_text('')
        failed = d.controller('check', inputs)
        assert failed['status'] == 'failed' and 'ssh_host_key' in json.dumps(failed)
        d.known_hosts.write_text(known)
        result['scenarios'].append('host-key-rejection')
        d.progress(suite="ssh_nginx", phase="durable-recovery")
        recovery(d, inputs)
        result['scenarios'].append('controller-death-reconciliation-resume')
        result['status'] = 'passed'
    finally:
        signal.signal(signal.SIGTERM, previous_termination)
        result['elapsed_ms'] = round((time.monotonic() - d.started) * 1000)
        try:
            if d.daemon_ready:
                try:
                    if d.container:
                        log = d.exec('cat', '/run/helix-ssh/server.log', check=False)
                        (d.artifacts / 'sshd.log').write_text(log.stdout + log.stderr)
                except BaseException:
                    result['status'] = 'failed'
                    raise
                finally:
                    d.cleanup()
            result['cleanup'] = 'passed'
        finally:
            if result.get('cleanup') != 'passed':
                result['status'] = 'failed'
            d.progress(suite='ssh_nginx', phase='complete', status=result['status'])
            result['commands'] = d.events
            (d.artifacts / 'ssh-report.yaml').write_text(yaml.safe_dump(result, sort_keys=False))
            print(f'ssh_nginx evidence: {d.artifacts.relative_to(h.root)}')
