"""Disposable remote escalation fixture, selected by HELIX_SSH_DOCKER=1."""
import json
from pathlib import Path
import subprocess
import time
import uuid


def verify_escalation(base, client_key, client_pub, ok, failed):
    versions = json.loads((Path(__file__).parents[1] /
                           'fixtures/docker_nginx/versions.json').read_text())
    name = 'helix-ssh-' + uuid.uuid4().hex
    image = name + ':test'
    context = base / 'docker'
    context.mkdir()
    (context / 'authorized_keys').write_text(client_pub + '\n')
    (context / 'Dockerfile').write_text('''ARG BASE
FROM ${BASE}
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server sudo && \
    useradd -m -g users -s /bin/sh operator && passwd -d operator && mkdir -p /run/sshd && \
    echo 'operator ALL=(root) NOPASSWD: ALL' > /etc/sudoers.d/operator
COPY authorized_keys /home/operator/.ssh/authorized_keys
RUN chown -R operator:users /home/operator/.ssh && chmod 700 /home/operator/.ssh && \
    chmod 600 /home/operator/.ssh/authorized_keys && ssh-keygen -A
CMD ["/usr/sbin/sshd", "-D", "-e", "-o", "PasswordAuthentication=no", "-o", "PermitRootLogin=no"]
''')

    def docker(*args, check=True):
        result = subprocess.run(['sudo', '-n', 'docker', *map(str, args)],
                                capture_output=True, text=True, timeout=300)
        if check and result.returncode:
            raise AssertionError(f'Docker {args[0]} failed ({result.returncode}): {result.stdout}\n{result.stderr}')
        return result

    try:
        docker('build', '--build-arg', 'BASE=' + versions['image'], '-t', image, context)
        docker('run', '-d', '--name', name, '-p', '127.0.0.1::22', image)
        port = docker('port', name, '22/tcp').stdout.strip().rsplit(':', 1)[1]
        known = context / 'known_hosts'
        key = docker('exec', name, 'cat', '/etc/ssh/ssh_host_ed25519_key.pub').stdout.split()
        known.write_text(f'[127.0.0.1]:{port} {key[0]} {key[1]}\n')
        flags = ['--allow-ssh', f'box,host=127.0.0.1,port={port},user=operator,known_hosts={known},identity={client_key},become=root,become=missing-user']
        for attempt in range(100):
            probe = subprocess.run(['ssh', '-F', '/dev/null', '-o', 'BatchMode=yes',
                                    '-o', 'StrictHostKeyChecking=yes', '-o', f'UserKnownHostsFile={known}',
                                    '-i', str(client_key), '-p', port, 'operator@127.0.0.1', 'true'],
                                   capture_output=True, timeout=5)
            if probe.returncode == 0:
                break
            time.sleep(0.1)
        else:
            raise AssertionError(probe.stderr)
        def command(program, args, **extra):
            return ok(dict(host='box', program=program, args=args, **extra), *flags)
        assert command('id', ['-un'])['stdout'] == 'operator\n'
        assert command('id', ['-un'], become='root')['stdout'] == 'root\n'
        assert command('sh', ['-c', 'cat; exit 255'], become='root', stdin='exact\r\nü') == dict(code=255, stdout='exact\r\nü', stderr='')
        path = '/root/helix-managed'
        ok(dict(host='box', path=path, text='root bytes', become='root'), *flags, operation='ssh_file_write')
        assert docker('exec', name, 'stat', '-c', '%u:%a', path).stdout.strip() == '0:644'
        assert docker('exec', name, 'cat', path).stdout == 'root bytes'
        assert ok(dict(host='box', path=path, become='root'), *flags, operation='ssh_file_read') == 'root bytes'
        failed(dict(host='box', program='touch', args=['/root/never'], become='missing-user'), 'ssh_privilege', *flags)
        assert docker('exec', name, 'test', '!', '-e', '/root/never').returncode == 0
        # Refusal is a real sudo policy denial, independent of sudo's language.
        docker('exec', name, 'rm', '/etc/sudoers.d/operator')
        failed(dict(host='box', program='touch', args=['/root/never'], become='root'), 'ssh_privilege', *flags)
        assert docker('exec', name, 'test', '!', '-e', '/root/never').returncode == 0
    finally:
        docker('rm', '-f', name, check=False)
        docker('image', 'rm', image, check=False)
