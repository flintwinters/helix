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
            program.write_text(json.dumps(call('let', {'http': call('import', 'std:http'), 'actor': call('import', 'std:actor'), 'json': call('import', 'std:json'), 'sqlite': call('import', 'std:sqlite')},
                call('http.serve', {'bind': '127.0.0.1:0', 'requests': requests, **(options or {})}, handler))))
            process = subprocess.Popen([str(h.binary), 'perform', '--allow-listen', '127.0.0.1:0',
                '--server-progress', str(progress), *flags, str(program)], cwd=h.root,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                ready = None
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if progress.exists():
                        contents = progress.read_text()
                        lines = contents.splitlines()
                        if lines and contents.endswith('\n'):
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
            call('call', fn(['sleep'], call('http.text', 200, 'slow')), call('effect', 'command', {'program': '/bin/sleep', 'args': ['.3']})), call('http.text', 200, 'fast')))
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

        counter = call('actor.make', 0, fn(['state', 'message'],
            call('dict', 'state', call('add', ref('state'), 1), 'reply', call('add', ref('state'), 1))))
        increment = fn(['r'], call('http.json_response', 200, call('actor.call', 'counter', None)))
        with server(increment, 8, options={'actors': {'counter': counter}}) as (host, port, _):
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                values = list(pool.map(lambda _: json.loads(request((host, port))[2]), range(8)))
            assert all(isinstance(value, int) for value in values), values
            assert sorted(values) == list(range(1, 9)), values

        # The same actor runs with two interchangeable storage compositions.
        import sqlite3
        for backend in ('json', 'sqlite'):
            path = base / ('state.json' if backend == 'json' else 'state.sqlite3')
            if backend == 'json':
                path.write_text('0')
                load = fn(['initial'], call('json.parse', call('effect', 'file_read', str(path))))
                save = fn(['next'], call('effect', 'file_write', {'path': str(path), 'text': call('json.dump', ref('next'))}))
                observe = lambda: json.loads(path.read_text())
            else:
                with sqlite3.connect(path) as db:
                    db.execute('CREATE TABLE state (id INTEGER PRIMARY KEY, value INTEGER)')
                    db.execute('INSERT INTO state VALUES (1, 0)')
                db.close()
                load = fn(['initial'], call('get', call('get', call('sqlite.query', str(path), 'SELECT value FROM state WHERE id=1', []), 0), 'value'))
                save = fn(['next'], call('sqlite.execute', str(path), 'UPDATE state SET value=? WHERE id=1', [ref('next')]))
                def observe():
                    with contextlib.closing(sqlite3.connect(path)) as db:
                        return db.execute('SELECT value FROM state WHERE id=1').fetchone()[0]
            persisted = call('actor.persist', {'load': load, 'save': save}, counter)
            for expected in (1, 2):
                with server(increment, flags=('--allow-read', str(base), '--allow-write', str(base)), options={'actors': {'counter': persisted}}) as (host, port, _):
                    assert json.loads(request((host, port))[2]) == expected
                    assert observe() == expected, (backend, expected)

        broken = call('actor.make', 0, fn(['state', 'message'], call('div', 1, 0)))
        with server(increment, 2, options={'actors': {'counter': broken}}) as (host, port, _):
            assert request((host, port))[0] == 500
            assert request((host, port))[0] == 500
        cyclic = {name: call('actor.make', 0, fn(['state', 'message'],
            {'state': 0, 'reply': call('actor.call', target, None)})) for name, target in [('a', 'b'), ('b', 'a')]}
        with server(fn(['r'], call('http.json_response', 200, call('actor.call', 'a', None))), options={'actors': cyclic}) as (host, port, _):
            started = time.monotonic()
            assert request((host, port))[0] == 500
            assert time.monotonic() - started < 2
