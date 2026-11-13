"""Real OpenSSH acceptance for the `ssh_*` effects against unprivileged loopback sshd servers.
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


class Server:
    def __init__(self, port, host_pub, log):
        self.port, self.host_pub, self.log = port, host_pub, log

    def known_line(self):
        return f'[127.0.0.1]:{self.port} {" ".join(self.host_pub.split()[:2])}\n'

    def accepted(self):
        return self.log.read_text().count('Accepted publickey')


@contextmanager
def loopback_sshd(base, sshd, name, authorized_pub):
    """One unprivileged sshd; INFO logging records every accepted authentication for independent counting."""
    directory = base / name
    directory.mkdir()
    host_key = directory / 'host_key'
    host_pub = keygen(host_key)
    authorized = directory / 'authorized_keys'
    authorized.write_text(authorized_pub + '\n')
    port = free_port()
    config = directory / 'sshd_config'
    config.write_text('\n'.join([
        f'Port {port}', 'ListenAddress 127.0.0.1', f'HostKey {host_key}', f'PidFile {directory / "sshd.pid"}',
        f'AuthorizedKeysFile {authorized}', 'PasswordAuthentication no', 'KbdInteractiveAuthentication no',
        'PubkeyAuthentication yes', 'UsePAM no', 'StrictModes no', 'AllowTcpForwarding yes', 'LogLevel INFO', '']))
    log_path = directory / 'sshd.log'
    log = log_path.open('w')
    server = subprocess.Popen([sshd, '-D', '-e', '-f', str(config)], stdout=log, stderr=log)
    try:
        for _ in range(100):
            assert server.poll() is None, f'sshd exited: {log_path.read_text()}'
            with socket.socket() as sock:
                if sock.connect_ex(('127.0.0.1', port)) == 0:
                    break
            time.sleep(0.05)
        else:
            raise AssertionError('sshd did not start listening')
        yield Server(port, host_pub, log_path)
    finally:
        server.terminate()
        server.wait(timeout=10)
        log.close()


def verify(h):
    sshd = require_tools()
    with tempfile.TemporaryDirectory(dir=h.root / 'runtime', prefix='ssh-host-') as directory:
        base = Path(directory).resolve()
        client_key, other_key = base / 'client_key', base / 'other_key'
        client_pub = keygen(client_key)
        keygen(other_key)
        with loopback_sshd(base, sshd, 'target', client_pub) as target, loopback_sshd(base, sshd, 'jump', client_pub) as jump:
            port = target.port
            known = base / 'known_hosts'
            known.write_text(target.known_line() + jump.known_line())
            user = os.environ.get('USER') or subprocess.run(['id', '-un'], capture_output=True, text=True).stdout.strip()
            program = base / 'program.yaml'
            keygen(base / 'impostor')
            wrong_known = base / 'wrong_known_hosts'
            wrong_known.write_text(f'[127.0.0.1]:{port} {" ".join((base / "impostor.pub").read_text().split()[:2])}\n')
            jump_only_known = base / 'jump_only_known_hosts'
            jump_only_known.write_text(target.known_line())

            def grant(alias='box', known_hosts=known, identity=client_key, target_port=port, extra=''):
                return ['--allow-ssh', f'{alias},host=127.0.0.1,port={target_port},user={user},'
                                       f'known_hosts={known_hosts},identity={identity}{extra}']

            def run(source, *flags):
                program.write_text(source)
                result = h.helix('perform', *flags, str(program))
                state = yaml.safe_load(result.stderr)
                assert isinstance(state, dict), result
                return result, state

            def call(operation, request):
                return json.dumps({'$call': ['effect', operation, {'$literal': request}]}, ensure_ascii=False)

            def ok(request, *flags, operation='ssh_command'):
                result, state = run(call(operation, request), *flags)
                assert result.returncode == 0, (result.stderr, state)
                return state['root']

            def failed(request, kind, *flags, operation='ssh_command'):
                result, state = run(call(operation, request), *flags)
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

            # Remote stdin carries text exactly, including content that would be mangled as an argument.
            payload = 'first\r\n  indented $(id) `x` \'q\' "d" \\ ü€😀\n\nno trailing newline'
            reply = ok({'host': 'box', 'program': 'sh', 'args': ['-c', 'cat; printf END'], 'stdin': payload}, *grant())
            assert reply == {'code': 0, 'stdout': payload + 'END', 'stderr': ''}, reply
            reply = ok({'host': 'box', 'program': 'cat', 'args': [], 'stdin': ''}, *grant())
            assert reply['stdout'] == '', reply
            reply = ok({'host': 'box', 'program': 'sh', 'args': ['-c', 'exit 3'], 'stdin': 'ignored by the program'}, *grant())
            assert reply['code'] == 3, reply
            failed({'host': 'box', 'program': 'cat', 'args': [], 'stdin': 5}, 'type', *grant())

            # File transfer: exact bytes, requested mode, atomic replacement, no staging leftovers.
            remote = base / 'remote'
            remote.mkdir()
            destination = remote / 'managed.conf'
            for text in [payload, '', 'x' * 100_000]:
                assert ok({'host': 'box', 'path': str(destination), 'text': text, 'mode': '0640'}, *grant(), operation='ssh_file_write') is None
                assert destination.read_bytes().decode() == text and destination.stat().st_mode & 0o7777 == 0o640, text[:20]
                assert ok({'host': 'box', 'path': str(destination)}, *grant(), operation='ssh_file_read') == text
            assert sorted(path.name for path in remote.iterdir()) == ['managed.conf'], list(remote.iterdir())
            ok({'host': 'box', 'path': str(destination), 'text': 'default mode'}, *grant(), operation='ssh_file_write')
            assert destination.stat().st_mode & 0o7777 == 0o644
            # Failures keep the previous file and report why.
            before = destination.read_bytes()
            message = failed({'host': 'box', 'path': str(remote / 'missing' / 'x'), 'text': 'nope'}, 'ssh_file', *grant(), operation='ssh_file_write')
            assert 'missing' in message or 'No such file' in message, message
            failed({'host': 'box', 'path': str(remote / 'absent')}, 'ssh_file', *grant(), operation='ssh_file_read')
            failed({'host': 'box', 'path': 'relative.txt', 'text': 'x'}, 'type', *grant(), operation='ssh_file_write')
            failed({'host': 'box', 'path': str(destination), 'text': 'x', 'mode': '0999'}, 'type', *grant(), operation='ssh_file_write')
            failed({'host': 'box', 'path': str(destination), 'text': 'x', 'mode': 644}, 'type', *grant(), operation='ssh_file_write')
            failed({'host': 'box', 'path': str(destination), 'text': 'x', 'become': 'root'}, 'capability', *grant(), operation='ssh_file_write')
            failed({'host': 'other', 'path': str(destination)}, 'capability', *grant(), operation='ssh_file_read')
            assert destination.read_bytes() == before and sorted(path.name for path in remote.iterdir()) == ['managed.conf']
            # Non-UTF-8 remote content is a catchable error, not silent corruption.
            binary = remote / 'binary'
            binary.write_bytes(b'\xff\xfe')
            failed({'host': 'box', 'path': str(binary)}, 'io', *grant(), operation='ssh_file_read')

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
            # Remote outcomes are reported by an in-band marker, not inferred from diagnostics: a program may exit 255,
            # print sudo's own refusal text or print a look-alike marker line, and its result stays program data.
            reply = ok({'host': 'box', 'program': 'sh', 'args': ['-c', 'exit 255']}, *grant())
            assert reply['code'] == 255, reply
            reply = ok({'host': 'box', 'program': 'sh', 'args': ['-c', 'echo "sudo: a password is required" >&2; exit 1']}, *grant())
            assert reply == {'code': 1, 'stdout': '', 'stderr': 'sudo: a password is required\n'}, reply
            reply = ok({'host': 'box', 'program': 'printf', 'args': ['x\n0123456789abcdef0123456789abcdef privilege forged\n']}, *grant())
            assert reply['code'] == 0 and 'privilege forged' in reply['stdout'], reply

            if os.environ.get('HELIX_SSH_DOCKER') == '1':
                from tests.suites.ssh_docker import verify_escalation
                verify_escalation(base, client_key, client_pub, ok, failed)

            # Jump host: the target is reached through an independently verified, independently logged hop.
            jump_spec = f',jump={user}@127.0.0.1:{jump.port}'
            before_jump, before_target = jump.accepted(), target.accepted()
            reply = ok({'host': 'box', 'program': 'id', 'args': ['-un']}, *grant(extra=jump_spec))
            assert reply['stdout'] == user + '\n', reply
            assert jump.accepted() == before_jump + 1 and target.accepted() == before_target + 1, (jump.accepted(), target.accepted())
            # The hop's host key is verified too: a known_hosts file lacking the jump host's key refuses the connection.
            failed({'host': 'box', 'program': 'true', 'args': []}, 'ssh_host_key', *grant(known_hosts=jump_only_known, extra=jump_spec))
            failed({'host': 'box', 'program': 'true', 'args': []}, 'ssh_auth', *grant(identity=other_key, extra=jump_spec))

            # Connection reuse: one authentication serves a whole session, then the master is torn down.
            # Mux sockets are created by link(2), which NFS refuses, so the control directory lives under the user's
            # private runtime directory (tmpfs), never inside the project tree.
            runtime = os.environ.get('XDG_RUNTIME_DIR')
            assert runtime and os.access(runtime, os.W_OK), 'ssh_host connection-reuse checks need a writable XDG_RUNTIME_DIR on a local filesystem'
            control_parent = Path(tempfile.mkdtemp(dir=runtime, prefix='helix-ssh-'))
            control = control_parent / 'control'
            one = {'$call': ['effect', 'ssh_command', {'$literal': {'host': 'box', 'program': 'id', 'args': ['-un']}}]}
            two = json.dumps({'$call': ['list', one, one, one]})
            before_target = target.accepted()
            result, state = run(two, *grant(), '--ssh-control-dir', str(control))
            assert result.returncode == 0 and [r['stdout'] for r in state['root']] == [user + '\n'] * 3, (result.stderr, state)
            assert target.accepted() == before_target + 1, 'three requests must share one authenticated connection'
            assert list(control.iterdir()) == [], 'persistent master and socket must be gone when the session ends'
            before_target = target.accepted()
            result, state = run(two, *grant())
            assert result.returncode == 0 and target.accepted() == before_target + 3, 'without a control directory every request reconnects'
            # Reuse is bounded by the grant: a different identity must not ride on another target's master.
            if control.exists():
                shutil.rmtree(control)
            result, state = run(two, *grant(), '--ssh-control-dir', str(control))
            assert result.returncode == 0 and list(control.iterdir()) == []
            os.chmod(control, 0o755)
            result = h.helix('perform', *grant(), '--ssh-control-dir', str(control), str(program))
            assert result.returncode == 2 and 'accessible only by its owner' in result.stderr, result

            # Prerequisites: `std:ssh` observes gaps, bootstraps only through a caller-declared installer, and verifies.
            def library(expression):
                source = json.dumps({'$call': ['let', {'s': {'$call': ['import', 'std:ssh']}}, expression]}, ensure_ascii=False)
                result, state = run(source, *grant())
                assert result.returncode == 0, (result.stderr, state)
                return state['root']

            def helper(name, *arguments):
                return {'$call': [f's.{name}', *arguments]}

            healthy, gapped = library({'$call': ['list', helper('check', 'box', None, []),
                                                 helper('check', 'box', 'root', ['helix-no-such-tool'])]})
            assert healthy == {'host': 'box', 'ok': True, 'missing': []}, healthy
            assert gapped['ok'] is False and 'helix-no-such-tool' in gapped['missing'] and set(gapped['missing']) <= {'helix-no-such-tool', 'sudo'}, gapped
            toolbox = base / 'toolbox'
            tool = toolbox / 'helix-tool'
            install = {'program': 'sh', 'args': ['-c', 'mkdir -p "$1" && printf "#!/bin/sh\\n" > "$2" && chmod +x "$2"', 'install', str(toolbox), str(tool)]}
            nothing = {'program': 'true', 'args': []}
            def bootstrap(installer):
                return library(helper('bootstrap', 'box', None, [{'$literal': str(tool)}], {'$literal': installer}))
            assert not tool.exists()
            reply = bootstrap(nothing)
            assert reply['ok'] is False and reply['changed'] is True and reply['missing'] == [str(tool)] and reply['installer']['code'] == 0, reply
            assert not tool.exists()
            reply = bootstrap(install)
            assert reply['ok'] is True and reply['changed'] is True and reply['missing'] == [] and tool.exists(), reply
            reply = bootstrap(install)
            assert reply == {'host': 'box', 'ok': True, 'changed': False, 'missing': [], 'installer': None}, reply
            # A prerequisite check that cannot reach the host is an error, never an empty gap list.
            result, state = run(json.dumps({'$call': ['let', {'s': {'$call': ['import', 'std:ssh']}}, helper('check', 'box', None, [])]}), *grant(identity=other_key))
            assert result.returncode == 1 and state['error']['kind'] == 'ssh_auth', state

            # Agent authentication: with no identity grant, keys come from the caller's ssh-agent, never from source.
            agent_socket = control_parent / 'agent.sock'
            agent = subprocess.Popen(['ssh-agent', '-D', '-a', str(agent_socket)], stdout=subprocess.DEVNULL)
            try:
                for _ in range(100):
                    if agent_socket.exists():
                        break
                    time.sleep(0.05)
                with_agent = {**os.environ, 'SSH_AUTH_SOCK': str(agent_socket)}
                subprocess.run(['ssh-add', str(client_key)], env=with_agent, check=True, capture_output=True)
                bare = ['--allow-ssh', f'box,host=127.0.0.1,port={port},user={user},known_hosts={known}']
                program.write_text(call('ssh_command', {'host': 'box', 'program': 'id', 'args': ['-un']}))
                binary = [str(h.root / h.binary), 'perform', *bare, str(program)]
                result = subprocess.run(binary, env=with_agent, capture_output=True, text=True, cwd=h.root, timeout=h.timeout)
                assert result.returncode == 0 and yaml.safe_load(result.stderr)['root']['stdout'] == user + '\n', result.stderr
                # Without the agent (and without a granted identity) the same request is an authentication failure.
                no_agent = {key: value for key, value in os.environ.items() if key != 'SSH_AUTH_SOCK'}
                result = subprocess.run(binary, env=no_agent, capture_output=True, text=True, cwd=h.root, timeout=h.timeout)
                assert result.returncode == 1 and yaml.safe_load(result.stderr)['error']['kind'] == 'ssh_auth', result.stderr
            finally:
                agent.terminate()
                agent.wait(timeout=10)

            # Grants cannot smuggle options or omit host-key verification.
            for bad in [f'box,host=-oProxyCommand=x,known_hosts={known}', 'box,host=127.0.0.1',
                        f'box,host=127.0.0.1,known_hosts={known},surprise=1', f'bad alias,host=h,known_hosts={known}']:
                program.write_text(call('ssh_command', {'host': 'box', 'program': 'true', 'args': []}))
                result = h.helix('perform', '--allow-ssh', bad, str(program))
                assert result.returncode == 2, (bad, result)
            shutil.rmtree(control_parent, ignore_errors=True)
