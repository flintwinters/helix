"""Controlled host-effect contracts. All temporary files stay under runtime/."""
import http.server
import json
import os
import sqlite3
from pathlib import Path
import tempfile
import threading
import time
import yaml


def verify(h):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', 'http://127.0.0.1:1/private')
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'local response')
        def log_message(self, *_):
            pass

    with tempfile.TemporaryDirectory(dir=h.root / 'runtime', prefix='effects-') as directory:
        base = Path(directory)
        program = base / 'program.yaml'
        def run(source, *flags, stdin=None):
            program.write_text(source)
            result = h.helix('perform', *flags, str(program), stdin=stdin)
            state = yaml.safe_load(result.stderr)
            assert isinstance(state, dict), result
            return result, state
        def denied(source, *flags):
            result, state = run(source, *flags)
            assert result.returncode == 1 and state['error']['kind'] == 'capability', state

        denied('{"$call": ["effect", "time", null]}')
        denied('{"$call": ["effect", "random", null]}')
        denied('{"$call": ["effect", "env", "PATH"]}')
        denied('{"$call": ["effect", "stdout", "secret"]}')
        denied('{"$call": ["effect", "stdin", null]}')
        denied('{"$call": ["effect", "command", {"program": "/bin/echo", "args": []}]}')
        denied('{"$call": ["effect", "http", "http://127.0.0.1/"]}')
        before = time.time_ns() // 1_000_000
        result, state = run('{"$call": ["effect", "time", null]}', '--allow-time')
        after = time.time_ns() // 1_000_000
        assert result.returncode == 0 and before <= state['root'] <= after, state
        result, state = run('{"$call": ["effect", "random", null]}', '--allow-random')
        assert result.returncode == 0 and type(state['root']) is int and 0 <= state['root'] < 2 ** 63, state
        missing_name = 'HELIX_EFFECT_MISSING_' + base.name.replace('-', '_')
        assert missing_name not in os.environ
        result, state = run(f'{{"$call": ["effect", "env", "{missing_name}"]}}', '--allow-env', missing_name)
        assert result.returncode == 0 and state['root'] is None, state
        for source, kind, flags in [
            ('{"$call": ["effect", "unknown_operation", null]}', 'effect', []),
            ('{"$call": ["effect", "exit", 256]}', 'type', []),
            ('{"$call": ["effect", "env", 1]}', 'type', []),
            ('{"$call": ["effect", "stdout", 1]}', 'type', ['--allow-stdout']),
            ('{"$call": ["effect", "command", {"program": "/bin/echo", "args": 1}]}', 'type', ['--allow-command', '/bin/echo']),
        ]:
            result, state = run(source, *flags)
            assert result.returncode == 1 and state['error']['kind'] == kind, state
        result, state = run('{"$call": ["effect", "command", {"program": "/bin/sh", "args": {"$call": ["list", "-c", "printf problem >&2; exit 3"]}}]}', '--allow-command', '/bin/sh')
        assert result.returncode == 0 and state['root'] == {'code': 3, 'stdout': '', 'stderr': 'problem'}, state
        captured = 'é雪' * 16384
        script = 'printf "%s" "$1"; printf "%s" "$1" >&2'
        result, state = run(
            f'{{"$call": ["effect", "command", {{"program": "/bin/sh", "args": {{"$call": ["list", "-c", {json.dumps(script)}, "capture", {json.dumps(captured, ensure_ascii=False)}]}}}}]}}',
            '--allow-command', '/bin/sh')
        assert result.returncode == 0 and state['root'] == {'code': 0, 'stdout': captured, 'stderr': captured}, state
        for script in ['printf "\\377"', 'printf "\\377" >&2']:
            result, state = run(
                f'{{"$call": ["effect", "command", {{"program": "/bin/sh", "args": {{"$call": ["list", "-c", {json.dumps(script)}]}}}}]}}',
                '--allow-command', '/bin/sh')
            assert result.returncode == 1 and state['error']['kind'] == 'io', state
        target = base / 'value.txt'
        target.write_text('original')
        module = base / 'module.yaml'
        module.write_text('value: 42')
        result, state = run('{"$call": ["import", "module.yaml"]}')
        assert result.returncode == 1, state
        result, state = run('{"$call": ["import", "module.yaml"]}', '--allow-read', str(base))
        assert result.returncode == 0 and state['root'] == {'value': 42}, state
        denied(f'{{"$call": ["effect", "file_read", {json.dumps(str(target))}]}}')
        denied(f'{{"$call": ["effect", "file_write", {{"path": {json.dumps(str(target))}, "text": "changed"}}]}}')
        assert target.read_text() == 'original'
        allowed = base / 'allowed'
        allowed.mkdir()
        (allowed / 'escape').symlink_to(target)
        denied(f"""{{"$call": ["effect", "file_read", {json.dumps(str(allowed / 'escape'))}]}}""", '--allow-read', str(allowed))
        denied(f"""{{"$call": ["effect", "file_write", {{"path": {json.dumps(str(allowed / 'escape'))}, "text": "changed"}}]}}""", '--allow-write', str(allowed))
        missing = base / 'must-not-exist'
        (allowed / 'dangling').symlink_to(missing)
        result, state = run(f"""{{"$call": ["effect", "file_write", {{"path": {json.dumps(str(allowed / 'dangling'))}, "text": "changed"}}]}}""", '--allow-write', str(allowed))
        assert result.returncode == 1 and not missing.exists(), state
        result, state = run('{"$call": ["try", {"$call": ["effect", "time", null]}, {"$call": ["fn", ["error"], {"$call": ["get", {"$ref": "error"}, "kind"]}]}]}')
        assert result.returncode == 0 and state['root'] == 'capability', state
        result, state = run('{"$call": ["type_time", {"$call": ["effect", "stdout", "must not print"]}]}', '--allow-stdout')
        assert result.returncode == 1 and result.stdout == '' and state['error']['phase'] == 'type', state
        database = base / 'data.sqlite3'
        db = json.dumps(str(database))
        def sql(statement, params='[]', write=False):
            return f'{{"$call": ["effect", "sqlite", {{"path": {db}, "sql": {json.dumps(statement)}, "params": {params}, "write": {json.dumps(write)}}}]}}'
        denied(sql('CREATE TABLE t (a)', write=True), '--allow-read', str(base))
        result, state = run(sql('CREATE TABLE t (id INTEGER PRIMARY KEY, name TEXT, v REAL)', write=True), '--allow-write', str(base))
        assert result.returncode == 0 and state['root'] == {'columns': [], 'rows': [], 'changes': 0}, state
        denied(sql('SELECT 1'), '--allow-write', str(base))
        result, state = run(sql('INSERT INTO t (name, v) VALUES (?, ?), (?, ?)', '["x", 1.5, null, 2]', write=True), '--allow-write', str(base))
        assert result.returncode == 0 and state['root']['changes'] == 2, state
        result, state = run(sql('SELECT id, name, v FROM t ORDER BY id'), '--allow-read', str(base))
        assert result.returncode == 0 and state['root'] == {'columns': ['id', 'name', 'v'], 'rows': [[1, 'x', 1.5], [2, None, 2.0]], 'changes': 0}, state
        for statement, write, kind in [('DELETE FROM t', False, 'sqlite'), ('SELECT 1; SELECT 2', False, 'sqlite'),
                                       (f"ATTACH {json.dumps(str(target))} AS other", True, 'sqlite'),
                                       ('SELECT x\'ff\'', False, 'conversion'), ('SELECT ?', False, 'type')]:
            params = '[[1]]' if kind == 'type' else '[]'
            result, state = run(sql(statement, params, write=write), '--allow-read', str(base), '--allow-write', str(base))
            assert result.returncode == 1 and state['error']['kind'] == kind, (statement, state)
        assert [row[0] for row in sqlite3.connect(database).execute('SELECT id FROM t')] == [1, 2]
        result, state = run("""{"$call": ["let", {"s": {"$call": ["import", "std:sqlite"]}},
            {"$call": ["list", {"$call": ["s.execute", %s, "INSERT INTO t (name) VALUES (?)", ["y"]]}, {"$call": ["s.query", %s, "SELECT name FROM t WHERE id > 1 ORDER BY id", []]}]}]}""" % (db, db),
            '--allow-read', str(base), '--allow-write', str(base))
        assert result.returncode == 0 and state['root'] == [1, [{'name': None}, {'name': 'y'}]], state
        huge = base / 'huge'
        huge.write_bytes(b'x' * (1024 * 1024 + 1))
        result, state = run(f'{{"$call": ["effect", "file_read", {json.dumps(str(huge))}]}}', '--allow-read', str(base))
        assert result.returncode == 1 and state['error']['kind'] == 'resource', state

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f'http://127.0.0.1:{server.server_port}'
            result, state = run(f'{{"$call": ["effect", "http", "{origin}/redirect"]}}', '--allow-http', origin)
            assert result.returncode == 0 and state['root']['status'] == 302, state
            denied('{"$call": ["effect", "http", "http://127.0.0.1:1/private"]}', '--allow-http', origin)
            flags = ['--allow-read', str(base), '--allow-write', str(base), '--allow-env', 'HELIX_EFFECT_TEST',
                     '--allow-stdin', '--allow-stdout', '--allow-command', '/bin/echo', '--allow-http', origin,
                     '--allow-time', '--allow-random', '--fixed-time', '123456', '--seed', '42']
            old = os.environ.get('HELIX_EFFECT_TEST')
            os.environ['HELIX_EFFECT_TEST'] = 'visible'
            try:
                outputs = [h.helix('perform', *flags, 'tests/cases/effects_world.yaml', '--', str(target), origin + '/', stdin='input text') for _ in range(2)]
            finally:
                if old is None:
                    del os.environ['HELIX_EFFECT_TEST']
                else:
                    os.environ['HELIX_EFFECT_TEST'] = old
            first, second = outputs
            assert (first.returncode, first.stdout, first.stderr) == (second.returncode, second.stdout, second.stderr)
            state = yaml.safe_load(first.stderr)
            assert first.returncode == 7 and first.stdout == 'hello from Helix', state
            root = state['root']
            assert root['text'] == 'hello, world' and root['environment'] == 'visible' and root['input'] == 'input text', root
            assert root['command'] == {'code': 0, 'stdout': 'captured\n', 'stderr': ''}, root
            assert root['response'] == {'status': 200, 'body': 'local response'} and root['time'] == 123456, root
            seed = 42
            expected_random = []
            for _ in range(2):
                seed = (seed * 6364136223846793005 + 1442695040888963407) % (2 ** 64)
                expected_random.append(seed >> 1)
            assert [root['random'], root['random_next']] == expected_random and root['exit'] is None, root
            assert len(state['effects']) == 12, state
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
