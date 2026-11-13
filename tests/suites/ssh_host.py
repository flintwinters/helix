"""Real OpenSSH acceptance for the `ssh_command` effect against an unprivileged loopback sshd.
All keys, configuration and logs stay under runtime/; nothing touches the user's ~/.ssh."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

import yaml


def require_tools():
    missing = [name for name in ('ssh', 'ssh-keygen') if not shutil.which(name)]
    sshd = shutil.which('sshd') or ('/usr/sbin/sshd' if os.access('/usr/sbin/sshd', os.X_OK) else None)
    if sshd is None:
        missing.append('sshd')
    assert not missing, f'ssh_host suite requires OpenSSH client and server binaries; missing: {", ".join(missing)}'
    return sshd


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def keygen(path):
    subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'helix-test', '-f', str(path)], check=True)
    return Path(f'{path}.pub').read_text().strip()


@contextmanager
def loopback_sshd(base, sshd):
    host_key, client_key, other_key = base / 'host_key', base / 'client_key', base / 'other_key'
    host_pub = keygen(host_key)
    client_pub = keygen(client_key)
    keygen(other_key)
    authorized = base / 'authorized_keys'
    authorized.write_text(client_pub + '\n')
    port = free_port()
    config = base / 'sshd_config'
    config.write_text('\n'.join([
        f'Port {port}', 'ListenAddress 127.0.0.1', f'HostKey {host_key}', f'PidFile {base / "sshd.pid"}',
        f'AuthorizedKeysFile {authorized}', 'PasswordAuthentication no', 'KbdInteractiveAuthentication no',
        'PubkeyAuthentication yes', 'UsePAM no', 'StrictModes no', 'LogLevel ERROR', '']))
    log = (base / 'sshd.log').open('w')
    server = subprocess.Popen([sshd, '-D', '-e', '-f', str(config)], stdout=log, stderr=log)
    try:
        for _ in range(100):
            assert server.poll() is None, f'sshd exited: {(base / "sshd.log").read_text()}'
            with socket.socket() as sock:
                if sock.connect_ex(('127.0.0.1', port)) == 0:
                    break
            time.sleep(0.05)
        else:
            raise AssertionError('sshd did not start listening')
        known = base / 'known_hosts'
        known.write_text(f'[127.0.0.1]:{port} {" ".join(host_pub.split()[:2])}\n')
        yield port, known, client_key, other_key
    finally:
        server.terminate()
        server.wait(timeout=10)
        log.close()


def verify(h):
    sshd = require_tools()
    with tempfile.TemporaryDirectory(dir=h.root / 'runtime', prefix='ssh-host-') as directory:
        base = Path(directory)
        with loopback_sshd(base, sshd) as (port, known, client_key, other_key):
            user = os.environ.get('USER') or subprocess.run(['id', '-un'], capture_output=True, text=True).stdout.strip()
            program = base / 'program.yaml'
            wrong_known = base / 'wrong_known_hosts'
            keygen(base / 'impostor')
            wrong_known.write_text(f'[127.0.0.1]:{port} {Path(base / "impostor.pub").read_text().split()[0]} '
                                   f'{Path(base / "impostor.pub").read_text().split()[1]}\n')

            def grant(alias='box', known_hosts=known, identity=client_key, target_port=port, extra=''):
                return ['--allow-ssh', f'{alias},host=127.0.0.1,port={target_port},user={user},'
                                       f'known_hosts={known_hosts},identity={identity}{extra}']

            def run(request, *flags):
                program.write_text(json.dumps({'$call': ['effect', 'ssh_command', {'$literal': request}]}, ensure_ascii=False))
                result = h.helix('perform', *flags, str(program))
                state = yaml.safe_load(result.stderr)
                assert isinstance(state, dict), result
                return result, state

            def ok(request, *flags):
                result, state = run(request, *flags)
                assert result.returncode == 0, (result.stderr, state)
                return state['root']

            def failed(request, kind, *flags):
                result, state = run(request, *flags)
                assert result.returncode == 1 and state['error']['kind'] == kind, (kind, state)
                return state['error']['message']

            # Remote output and exit status are data; the remote working account matches the grant.
            reply = ok({'host': 'box', 'program': 'id', 'args': ['-un']}, *grant())
            assert reply == {'code': 0, 'stdout': user + '\n', 'stderr': ''}, reply
            reply = ok({'host': 'box', 'program': 'sh', 'args': ['-c', 'echo out; echo err >&2; exit 7']}, *grant())
            assert reply == {'code': 7, 'stdout': 'out\n', 'stderr': 'err\n'}, reply

            # Argument bytes reach the remote program unchanged: no word splitting, expansion or substitution.
            awkward = ['two words', "it's", '$HOME', '`id`', '$(id)', 'a;b', 'a|b', '*', '', '-n', 'line1\nline2', '"dq"', '\\']
            reply = ok({'host': 'box', 'program': 'printf', 'args': ['%s\\0'] + awkward}, *grant())
            assert reply['code'] == 0 and reply['stdout'].split('\0')[:-1] == awkward, reply
            reply = ok({'host': 'box', 'program': 'printf', 'args': ['%s', 'ü€😀']}, *grant())
            assert reply['stdout'] == 'ü€😀', reply

            # Authority comes only from caller grants.
            failed({'host': 'box', 'program': 'true', 'args': []}, 'capability')
            failed({'host': 'other', 'program': 'true', 'args': []}, 'capability', *grant())
            failed({'host': 'box', 'program': 'true', 'args': [], 'become': 'root'}, 'capability', *grant())
            failed({'host': 'box', 'program': 'true', 'args': ['x', 1]}, 'type', *grant())
            failed({'host': 'box', 'program': 'a\0b', 'args': []}, 'type', *grant())

            # Connection, host-key, authentication and privilege failures stay distinguishable.
            failed({'host': 'box', 'program': 'true', 'args': []}, 'ssh_host_key', *grant(known_hosts=wrong_known))
            failed({'host': 'box', 'program': 'true', 'args': []}, 'ssh_host_key', *grant(known_hosts=base / 'absent_known_hosts'))
            failed({'host': 'box', 'program': 'true', 'args': []}, 'ssh_auth', *grant(identity=other_key))
            failed({'host': 'box', 'program': 'true', 'args': []}, 'ssh_connection', *grant(target_port=free_port()))
            if shutil.which('sudo'):
                # The test account normally lacks passwordless sudo; the refusal is a privilege failure, not a program exit.
                probe = subprocess.run(['sudo', '-n', 'true'], capture_output=True)
                if probe.returncode != 0:
                    failed({'host': 'box', 'program': 'true', 'args': [], 'become': 'root'}, 'ssh_privilege', *grant(extra=',become=root'))

            # Grants cannot smuggle options or omit host-key verification.
            for bad in [f'box,host=-oProxyCommand=x,known_hosts={known}', 'box,host=127.0.0.1',
                        f'box,host=127.0.0.1,known_hosts={known},surprise=1', f'bad alias,host=h,known_hosts={known}']:
                program.write_text('{"$call": ["effect", "ssh_command", {"$literal": {"host": "box", "program": "true", "args": []}}]}')
                result = h.helix('perform', '--allow-ssh', bad, str(program))
                assert result.returncode == 2, (bad, result)
