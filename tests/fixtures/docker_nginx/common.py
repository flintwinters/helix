"""Fixed mechanism boundaries shared by the container command and PID 1."""
import json
from pathlib import Path
import socket
import time

RUN = Path('/run/helix-nginx')
DATA = Path('/var/lib/helix-nginx-host')
CONFIG = Path('/etc/helix-nginx/nginx.conf')
SERVER = Path('/etc/helix-nginx/server.conf')
CONTENT = Path('/var/lib/helix-nginx/index.html')
FILES = (CONFIG, SERVER, CONTENT)
SOCKET = RUN / 'supervisor.sock'


def event(operation, **details):
    """Completed mutations are fixture evidence, never a planning input."""
    DATA.mkdir(parents=True, exist_ok=True)
    with (DATA / 'actions.jsonl').open('a') as stream:
        stream.write(json.dumps({'operation': operation, 'phase': 'completed', **details}) + '\n')
        stream.flush()


def supervisor(operation):
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(12)
        client.connect(str(SOCKET))
        client.sendall(json.dumps({'operation': operation}).encode() + b'\n')
        with client.makefile('rb') as stream:
            raw = stream.readline(65537)
        if len(raw) > 65536:
            raise RuntimeError('oversized supervisor response')
        result = json.loads(raw)
        if 'error' in result:
            raise RuntimeError(result['error'])
        return result


def wait_until(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.05)
    raise TimeoutError('readiness deadline exceeded')
