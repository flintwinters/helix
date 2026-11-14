"""Independent loopback clients and process lifecycle, part of effects suite."""
import concurrent.futures
import contextlib
import http.client
import json
import socket
import subprocess
import tempfile
import time
from pathlib import Path
import yaml


def call(name, *args):
    return {'$call': [name, *args]}


def ref(name):
    return {'$ref': name}


def fn(params, body):
    return call('fn', params, body)


def verify(h):
    with tempfile.TemporaryDirectory(dir=h.root / 'runtime', prefix='http-') as directory:
        base = Path(directory)
        serial = 0

        @contextlib.contextmanager
        def server(handler, requests=1, flags=(), options=None):
            nonlocal serial
            serial += 1
            program = base / f'program-{serial}.yaml'
            progress = base / f'progress-{serial}.jsonl'
            program.write_text(json.dumps(call('let', {'http': call('import', 'std:http')},
                call('http.serve', {'bind': '127.0.0.1:0', 'requests': requests, **(options or {})}, handler))))
            process = subprocess.Popen([str(h.binary), 'perform', '--allow-listen', '127.0.0.1:0',
                '--server-progress', str(progress), *flags, str(program)], cwd=h.root,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                ready = None
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if progress.exists():
                        lines = progress.read_text().splitlines()
                        if lines:
                            ready = json.loads(lines[0])
                            break
                    assert process.poll() is None, process.communicate()
                    time.sleep(.01)
                assert ready and ready['event'] == 'ready', ready
                host, port = ready['address'].rsplit(':', 1)
                yield host, int(port), process
                stdout, stderr = process.communicate(timeout=15)
                assert process.returncode == 0 and not stdout, (process.returncode, stdout, stderr)
                state = yaml.safe_load(stderr)
                assert state['status'] == 'finished', state
                assert json.loads(progress.read_text().splitlines()[-1])['event'] == 'shutdown'
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.communicate(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()

        def request(address, method='GET', target='/', body=None, headers=None):
            connection = http.client.HTTPConnection(*address, timeout=12)
            try:
                connection.request(method, target, body, headers or {})
                response = connection.getresponse()
                return response.status, response.getheaders(), response.read()
            finally:
                connection.close()

        echo = fn(['r'], call('http.json_response', 200, ref('r')))
        with server(echo, 2) as (host, port, _):
            status, headers, body = request((host, port), 'POST', '/echo?x=1', 'hello')
            data = json.loads(body)
            assert status == 200 and data['method'] == 'POST' and data['path'] == '/echo'
            assert data['query'] == 'x=1' and data['body'] == 'hello', data
            status, _, body = request((host, port), 'HEAD')
            assert status == 200 and body == b''

        with server(fn(['r'], call('div', 1, 0))) as (host, port, _):
            status, _, body = request((host, port))
            assert status == 500 and b'div' not in body

        slow = fn(['r'], call('if', call('eq', ref('r.path'), '/slow'),
            call('let', {'sleep': call('effect', 'command', {'program': '/bin/sleep', 'args': ['.3']})},
                 call('http.text', 200, 'slow')), call('http.text', 200, 'fast')))
        with server(slow, 2, ('--allow-command', '/bin/sleep')) as (host, port, _):
            with concurrent.futures.ThreadPoolExecutor() as pool:
                pending = pool.submit(request, (host, port), 'GET', '/slow')
                time.sleep(.05)
                started = time.monotonic()
                assert request((host, port))[2] == b'fast'
                assert time.monotonic() - started < .25
                assert pending.result()[2] == b'slow'

        with server(echo) as (host, port, _):
            assert request((host, port), 'POST', '/', 'x' * (256 * 1024 + 1))[0] == 413

        program = base / 'denied.yaml'
        program.write_text(json.dumps(call('http_serve', {'bind': '127.0.0.1:0', 'requests': 1}, fn(['r'], None))))
        denied = h.helix('perform', str(program))
        assert denied.returncode == 1 and yaml.safe_load(denied.stderr)['error']['kind'] == 'permission', denied
        journal = base / 'journal.json'
        denied = h.helix('perform', '--allow-listen', '127.0.0.1:0', '--checkpoint', str(journal), str(program))
        assert denied.returncode == 1 and 'journaled server' in denied.stderr, denied
