#!/usr/bin/env python3
"""Container-local PID 1 owns nginx; effect subprocesses only send explicit requests.

There is no desired-state loop or automatic restart. Helix chooses each action.
"""
import json
import os
import signal
import socket
import subprocess
import uuid

from common import CONFIG, DATA, RUN, SOCKET, event


def main():
    RUN.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    identity = DATA / 'target-id'
    if not identity.exists():
        identity.write_text(str(uuid.uuid4()))
    (RUN / 'supervisor.pid').write_text(str(os.getpid()))
    (RUN / 'fault-release').unlink(missing_ok=True)
    child = None
    stopping = False
    sentinel = subprocess.Popen(['sleep', 'infinity'], start_new_session=True)
    (RUN / 'sentinel.pid').write_text(str(sentinel.pid))

    def stop_signal(signum, frame):
        nonlocal stopping
        stopping = True

    def stop_nginx():
        nonlocal child
        if child is not None and child.poll() is None:
            os.kill(child.pid, signal.SIGQUIT)
            try:
                child.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=2)
            event('service-stop', pid=child.pid)
        child = None

    def reap():
        # Peek first so Popen owns collection of its tracked child's exit status.
        # waitpid(-1) could otherwise consume that status between two polls.
        while True:
            try:
                ended = os.waitid(os.P_ALL, 0, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            except ChildProcessError:
                return
            if ended is None:
                return
            if child is not None and ended.si_pid == child.pid:
                child.poll()
            elif ended.si_pid == sentinel.pid:
                sentinel.poll()
            else:
                os.waitpid(ended.si_pid, 0)

    def bound_log():
        path = DATA / 'nginx.log'
        if path.exists() and path.stat().st_size > 1048576:
            # The writer uses O_APPEND. Retain recent failure evidence without
            # letting deliberately noisy invalid configurations fill the layer.
            with path.open('r+b') as stream:
                stream.seek(-65536, os.SEEK_END)
                tail = stream.read(65536)
                stream.seek(0)
                stream.write(b'[older fixture output truncated]\n' + tail)
                stream.truncate()

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop_signal)
    SOCKET.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(SOCKET))
        os.chmod(SOCKET, 0o600)
        server.listen(8)
        server.settimeout(0.2)
        try:
            while not stopping:
                reap()
                bound_log()
                try:
                    connection, _ = server.accept()
                except socket.timeout:
                    continue
                with connection:
                    connection.settimeout(2)
                    try:
                        with connection.makefile('rb') as stream:
                            raw = stream.readline(65537)
                        if len(raw) > 65536:
                            raise ValueError('oversized supervisor request')
                        operation = json.loads(raw)['operation']
                        running = child is not None and child.poll() is None
                        if operation == 'status':
                            reply = {'running': running, 'pid': child.pid if running else None}
                        elif operation == 'start':
                            if running:
                                raise RuntimeError('nginx already running')
                            with (DATA / 'nginx.log').open('ab', buffering=0) as log:
                                child = subprocess.Popen(['/usr/sbin/nginx', '-c', str(CONFIG), '-g', 'daemon off;'],
                                                         stdout=log, stderr=log, start_new_session=True)
                            event('service-start', pid=child.pid)
                            reply = {'pid': child.pid}
                        elif operation == 'reload':
                            if not running:
                                raise RuntimeError('nginx is not running')
                            os.kill(child.pid, signal.SIGHUP)
                            event('service-reload', pid=child.pid)
                            reply = {'pid': child.pid}
                        elif operation == 'stop':
                            stop_nginx()
                            reply = {'stopped': True}
                        else:
                            raise ValueError('unsupported supervisor operation')
                    except (KeyError, TypeError, ValueError, OSError, RuntimeError, subprocess.SubprocessError) as error:
                        reply = {'error': str(error)}
                    try:
                        connection.sendall(json.dumps(reply).encode() + b'\n')
                    except OSError:
                        # A killed controller may lose the acknowledgement. Its
                        # disappearance must not terminate the service supervisor.
                        pass
        finally:
            stop_nginx()
            sentinel.terminate()
            sentinel.wait(timeout=2)
            SOCKET.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
