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

from tests.suites.test_reports import completed_documents


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
        def server(handler, requests=1, flags=(), options=None, expected_exit=0, modules=('actor', 'json', 'sqlite')):
            nonlocal serial
            serial += 1
            program = base / f'program-{serial}.yaml'
            progress = base / f'progress-{serial}.yaml'
            program.write_text(json.dumps(call('let', {'http': call('import', 'std:http'), **{name: call('import', 'std:' + name) for name in modules}},
                call('http.serve', {'bind': '127.0.0.1:0', 'requests': requests, **(options or {})}, handler))))
            process = subprocess.Popen([str(h.binary), 'perform', '--allow-listen', '127.0.0.1:0',
                '--server-progress', str(progress), *flags, str(program)], cwd=h.root,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                ready = None
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    records = completed_documents(progress)
                    if records:
                        ready = records[0]
                        break
                    assert process.poll() is None, process.communicate()
                    time.sleep(.01)
                assert ready and ready['event'] == 'ready', ready
                host, port = ready['address'].rsplit(':', 1)
                yield host, int(port), process
                stdout, stderr = process.communicate(timeout=15)
                assert process.returncode == expected_exit and not stdout, (process.returncode, stdout, stderr)
                state = yaml.safe_load(stderr)
                assert state['status'] == 'finished', state
                assert completed_documents(progress)[-1]['event'] == 'shutdown'
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.communicate(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.communicate()

        def request(address, method='GET', target='/', body=None, headers=None):
            connection = http.client.HTTPConnection(*address, timeout=45)
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

        assets = base / 'assets'
        assets.mkdir()
        binary = bytes(range(256)) * 32
        (assets / 'binary.dat').write_bytes(binary)
        (assets / 'escape').symlink_to(base / 'denied.yaml')
        static = call('http.static_directory', str(assets))
        with server(static, 5, flags=('--allow-read', str(assets))) as (host, port, _):
            assert request((host, port), target='/binary.dat')[2] == binary
            assert request((host, port), 'HEAD', '/binary.dat')[2] == b''
            for path in ('/../denied.yaml', '/%2e%2e/denied.yaml', '/escape'):
                assert request((host, port), target=path)[0] == 403
        with server(static) as (host, port, _):
            assert request((host, port), target='/binary.dat')[0] == 403

        # A prohibited write must never reach its independently granted target.
        prohibited = base / 'must-not-exist'
        write = fn(['r'], call('call', fn(['done'], call('http.text', 200, 'bad')),
            call('effect', 'file_write', {'path': str(prohibited), 'text': 'bad'})))
        pure = {'type': 'promise', 'name': 'pure', 'requires': [{'effects': []}]}
        with server(write, flags=('--allow-write', str(base)), options={'promise': pure}) as (host, port, _):
            assert request((host, port))[0] == 500
            assert not prohibited.exists()
        reversible = {'type': 'promise', 'name': 'reversible', 'requires': ['reversible']}
        with server(fn(['r'], {'status': 200, 'headers': [], 'body': 'pure'}), flags=('--server-deadline-ms', '30000'), options={'promise': reversible}, modules=()) as (host, port, _):
            pure_reply = request((host, port))
            assert pure_reply[2] == b'pure', pure_reply

        with server(slow, flags=('--allow-command', '/bin/sleep', '--server-deadline-ms', '100')) as (host, port, _):
            assert request((host, port), target='/slow')[0] == 504

        # Completed request machines must not accumulate with sustained traffic.
        with server(fn(['r'], call('http.text', 200, 'x' * 65536)), requests=512) as (host, port, process):
            def rss():
                status = Path(f'/proc/{process.pid}/status').read_text()
                return int(status.split('VmRSS:')[1].split()[0])
            for _ in range(32):
                assert len(request((host, port))[2]) == 65536
            before = rss()
            for _ in range(240):
                assert len(request((host, port))[2]) == 65536
            # Allocator slack is bounded; retaining all results alone costs 30 MiB.
            assert rss() - before < 24 * 1024
            for _ in range(240):
                assert len(request((host, port))[2]) == 65536

        # Persistence rejection cannot publish a reply or leave the actor usable.
        failing_storage = {'load': fn(['initial'], ref('initial')), 'save': fn(['next'], call('raise', 'save failed'))}
        with server(increment, 2, options={'actors': {'counter': call('actor.persist', failing_storage, counter)}}) as (host, port, _):
            assert request((host, port))[0] == 500
            assert request((host, port))[0] == 500

        with server(echo) as (host, port, _):
            client = http.client.HTTPConnection(host, port, timeout=10)
            client.putrequest('GET', '/')
            client.putheader('x-duplicate', 'one')
            client.putheader('x-duplicate', 'two')
            client.endheaders()
            response = client.getresponse()
            data = json.loads(response.read())
            client.close()
            assert [value for name, value in data['headers'] if name == 'x-duplicate'] == ['one', 'two']
        with server(echo) as (host, port, _):
            for wire, expected in [(b'INVALID\r\n\r\n', b'400'),
                    (b'GET / HTTP/1.1\r\nHost: localhost\r\nX: ' + b'x' * 20000 + b'\r\n\r\n', b'431')]:
                with socket.create_connection((host, port), timeout=10) as client:
                    client.sendall(wire)
                    assert expected in client.recv(4096).split(b'\r\n')[0]
            assert request((host, port))[0] == 200

        for operation, payload in [('stdin', None), ('exit', 17)]:
            with server(fn(['r'], call('effect', operation, payload)), flags=('--allow-stdin',)) as (host, port, _):
                assert request((host, port))[0] == 500
        with server(fn(['r'], call('http_serve', {'bind': '127.0.0.1:0'}, fn(['r'], None)))) as (host, port, _):
            assert request((host, port))[0] == 500
        with server(fn(['r'], call('actor.call', 'unknown', None))) as (host, port, _):
            assert request((host, port))[0] == 500

        busy = call('actor.make', 0, fn(['state', 'message'], call('call',
            fn(['slept'], call('dict', 'state', call('add', ref('state'), 1), 'reply', call('add', ref('state'), 1))),
            call('effect', 'command', {'program': '/bin/sleep', 'args': ['.3']}))))
        with server(increment, 16, flags=('--allow-command', '/bin/sleep', '--server-handlers', '32', '--server-mailbox', '1'),
                    options={'actors': {'counter': busy}}) as (host, port, _):
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                replies = list(pool.map(lambda _: request((host, port))[0], range(16)))
            assert 200 in replies and 500 in replies and set(replies) <= {200, 500}, replies

        with server(fn(['r'], call('http.text', 200, 'alive')), requests=100000, expected_exit=1) as (host, port, process):
            assert request((host, port))[2] == b'alive'
            process.terminate()

        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            address = '127.0.0.1:' + str(occupied.getsockname()[1])
            program = base / 'conflict.yaml'
            program.write_text(json.dumps(call('http_serve', {'bind': address}, fn(['r'], None))))
            conflict = h.helix('perform', '--allow-listen', address, str(program))
            assert conflict.returncode == 1 and yaml.safe_load(conflict.stderr)['error']['kind'] == 'io'

        marker = base / 'started'
        blocked = fn(['r'], call('call', fn(['done'], call('http.text', 200, 'done')),
            call('effect', 'command', {'program': '/bin/sh', 'args': ['-c', 'printf started > "$1"; sleep .5', 'helix-http', str(marker)]})))
        with server(blocked, 2, flags=('--allow-command', '/bin/sh', '--server-handlers', '1', '--server-deadline-ms', '150')) as (host, port, _):
            assert request((host, port))[0] == 504
            assert marker.read_text() == 'started'
            assert request((host, port))[0] == 503, 'timed-out blocking effect released capacity early'
