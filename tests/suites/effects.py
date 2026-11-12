"""Controlled host-effect contracts. All temporary files stay under runtime/."""
import http.server
import json
import os
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

        denied("[effect, 'time', null]")
        denied("[effect, 'random', null]")
        denied("[effect, 'env', 'PATH']")
        denied("[effect, 'stdout', 'secret']")
        denied("[effect, 'stdin', null]")
        denied("[effect, 'command', {program: '/bin/echo', args: []}]")
        denied("[effect, 'http', 'http://127.0.0.1/']")
        before = time.time_ns() // 1_000_000
        result, state = run("[effect, 'time', null]", '--allow-time')
        after = time.time_ns() // 1_000_000
        assert result.returncode == 0 and before <= state['root'] <= after, state
        result, state = run("[effect, 'random', null]", '--allow-random')
        assert result.returncode == 0 and type(state['root']) is int and 0 <= state['root'] < 2 ** 63, state
        missing_name = 'HELIX_EFFECT_MISSING_' + base.name.replace('-', '_')
        assert missing_name not in os.environ
        result, state = run(f"[effect, 'env', '{missing_name}']", '--allow-env', missing_name)
        assert result.returncode == 0 and state['root'] is None, state
        for source, kind, flags in [
            ("[effect, 'unknown_operation', null]", 'effect', []),
            ("[effect, 'exit', 256]", 'type', []),
            ("[effect, 'env', 1]", 'type', []),
            ("[effect, 'stdout', 1]", 'type', ['--allow-stdout']),
            ("[effect, 'command', {program: '/bin/echo', args: 1}]", 'type', ['--allow-command', '/bin/echo']),
        ]:
            result, state = run(source, *flags)
            assert result.returncode == 1 and state['error']['kind'] == kind, state
        result, state = run("[effect, 'command', {program: '/bin/sh', args: [list, '-c', 'printf problem >&2; exit 3']}]", '--allow-command', '/bin/sh')
        assert result.returncode == 0 and state['root'] == {'code': 3, 'stdout': '', 'stderr': 'problem'}, state
        captured = 'é雪' * 16384
        script = 'printf "%s" "$1"; printf "%s" "$1" >&2'
        result, state = run(
            f"[effect, 'command', {{program: '/bin/sh', args: [list, '-c', {json.dumps(script)}, 'capture', {json.dumps(captured, ensure_ascii=False)}]}}]",
            '--allow-command', '/bin/sh')
        assert result.returncode == 0 and state['root'] == {'code': 0, 'stdout': captured, 'stderr': captured}, state
        for script in ['printf "\\377"', 'printf "\\377" >&2']:
            result, state = run(
                f"[effect, 'command', {{program: '/bin/sh', args: [list, '-c', {json.dumps(script)}]}}]",
                '--allow-command', '/bin/sh')
            assert result.returncode == 1 and state['error']['kind'] == 'io', state
        target = base / 'value.txt'
        target.write_text('original')
        module = base / 'module.yaml'
        module.write_text('value: 42')
        result, state = run("[import, 'module.yaml']")
        assert result.returncode == 1, state
        result, state = run("[import, 'module.yaml']", '--allow-read', str(base))
        assert result.returncode == 0 and state['root'] == {'value': 42}, state
        denied(f"[effect, 'file_read', {json.dumps(str(target))}]")
        denied(f"[effect, 'file_write', {{path: {json.dumps(str(target))}, text: 'changed'}}]")
        assert target.read_text() == 'original'
        allowed = base / 'allowed'
        allowed.mkdir()
        (allowed / 'escape').symlink_to(target)
        denied(f"[effect, 'file_read', {json.dumps(str(allowed / 'escape'))}]", '--allow-read', str(allowed))
        denied(f"[effect, 'file_write', {{path: {json.dumps(str(allowed / 'escape'))}, text: 'changed'}}]", '--allow-write', str(allowed))
        missing = base / 'must-not-exist'
        (allowed / 'dangling').symlink_to(missing)
        result, state = run(f"[effect, 'file_write', {{path: {json.dumps(str(allowed / 'dangling'))}, text: 'changed'}}]", '--allow-write', str(allowed))
        assert result.returncode == 1 and not missing.exists(), state
        result, state = run("[try, [effect, 'time', null], [fn, [error], [get, error, 'kind']]]")
        assert result.returncode == 0 and state['root'] == 'capability', state
        result, state = run("[type_time, [effect, 'stdout', 'must not print']]", '--allow-stdout')
        assert result.returncode == 1 and result.stdout == '' and state['error']['phase'] == 'type', state
        huge = base / 'huge'
        huge.write_bytes(b'x' * (1024 * 1024 + 1))
        result, state = run(f"[effect, 'file_read', {json.dumps(str(huge))}]", '--allow-read', str(base))
        assert result.returncode == 1 and state['error']['kind'] == 'resource', state

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f'http://127.0.0.1:{server.server_port}'
            result, state = run(f"[effect, 'http', '{origin}/redirect']", '--allow-http', origin)
            assert result.returncode == 0 and state['root']['status'] == 302, state
            denied("[effect, 'http', 'http://127.0.0.1:1/private']", '--allow-http', origin)
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
