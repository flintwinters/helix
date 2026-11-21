"""Serves index.html and runs the helix CLI on posted source. Usage: python3 editor/web/serve.py [port]"""
import http.server, json, re, pathlib, subprocess, sys, urllib.parse
HERE = pathlib.Path(__file__).parent
sys.path.insert(0, str(HERE))
import workbook
BIN = HERE.parents[1] / 'target' / 'debug' / 'helix'
ROOT = HERE.parents[1]
LIBRARIES = ('examples', 'std')  # the only trees the browser may list and read

IMPORT = re.compile(r'import,\s*"?std:(\w+)')

def depth(path, seen=()):
    """Import depth under std/: 0 for self-contained files, else 1 + the deepest imported module."""
    deps = {m for m in IMPORT.findall(path.read_text()) if (ROOT / 'std' / f'{m}.yaml') not in seen + (path,)}
    return 1 + max(depth(ROOT / 'std' / f'{m}.yaml', seen + (path,)) for m in deps) if deps else 0

def library_files():
    """Learning order within each tree: fewest transitive imports first, then shortest."""
    def key(f): return depth(f), len(f.read_text().splitlines()), f.name
    return [{'group': top, 'path': f.relative_to(ROOT).as_posix(), 'depth': depth(f)}
            for top in LIBRARIES for f in sorted((ROOT / top).rglob('*.yaml'), key=key)]

STATIC = {'/helix-mode.js': 'helix-mode.js', '/workbook.js': 'workbook.js'}  # the only scripts the page may load

def run(args, stdin):
    """The workbook grader's runner: (returncode, stdout, stderr) of one helix invocation."""
    done = subprocess.run([BIN, *args], input=stdin.encode(), capture_output=True, timeout=30)
    return done.returncode, done.stdout.decode(), done.stderr.decode()

def query(url, name):
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get(name, [''])[0]

COMMANDS = {'run': ['run'], 'check': ['check', '--evaluate'], 'fmt': ['fmt'], 'step': ['run', '--annotations']}

class Handler(http.server.BaseHTTPRequestHandler):
    def reply(self, body, status=200, kind='text/plain'):
        self.send_response(status); self.send_header('Content-Type', kind + '; charset=utf-8'); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == '/builtins':  # actor names and families for highlighting, from the binary itself
            done = subprocess.run([BIN, 'builtins', '--json'], capture_output=True, timeout=30)
            return self.reply(done.stdout if done.returncode == 0 else b'[]', kind='application/json')
        if path == '/files':
            return self.reply(json.dumps(library_files()).encode(), kind='application/json')
        if path == '/file':  # only paths /files lists, so no traversal outside examples/ and std/
            wanted = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('path', [''])[0]
            if wanted not in {f['path'] for f in library_files()}: return self.reply(b'not a library file', 404)
            return self.reply((ROOT / wanted).read_bytes())
        if path in STATIC:
            return self.reply((HERE / STATIC[path]).read_bytes(), kind='text/javascript')
        if path == '/workbook.json':  # solutions stay server-side until asked for
            try: book = [{**c, 'steps': [{k: v for k, v in step.items() if k != 'solution'} | {'has_solution': 'solution' in step}
                                          for step in c['steps']]} for c in workbook.chapters()]
            except ImportError: return self.reply(b'the workbook needs PyYAML (pip install pyyaml)', 503)
            return self.reply(json.dumps(book).encode(), kind='application/json')
        if path == '/solution':
            try: return self.reply(workbook.step_named(query(self.path, 'id')).get('solution', '').encode())
            except KeyError: return self.reply(b'unknown step', 404)
        self.reply((HERE / 'index.html').read_bytes(), kind='text/html')
    def do_POST(self):
        if urllib.parse.urlparse(self.path).path == '/grade':
            source = self.rfile.read(int(self.headers['Content-Length'])).decode()
            try: step = workbook.step_named(query(self.path, 'id'))
            except KeyError: return self.reply(b'unknown step', 404)
            try: return self.reply(json.dumps(workbook.grade(step, source, run)).encode(), kind='application/json')
            except subprocess.TimeoutExpired: return self.reply(b'timed out after 30s', 504)
        command = COMMANDS.get(urllib.parse.urlparse(self.path).path.strip('/'))
        if not command: return self.reply(b'unknown command', 404)
        source = self.rfile.read(int(self.headers['Content-Length']))
        try:
            done = subprocess.run([BIN, *command, '-'], input=source, capture_output=True, timeout=30)
            if command[0] == 'fmt' and done.returncode:  # a failed format must never replace the buffer
                return self.reply(done.stdout + done.stderr, 422)
            if command[0] != 'fmt' and done.returncode == 0:  # results are YAML: show them canonically formatted
                pretty = subprocess.run([BIN, 'fmt', '-'], input=done.stdout, capture_output=True, timeout=30)
                if pretty.returncode == 0: done.stdout = pretty.stdout
            self.reply(done.stdout + done.stderr)
        except subprocess.TimeoutExpired:
            self.reply(b'timed out after 30s')

http.server.ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1]) if len(sys.argv) > 1 else 8765), Handler).serve_forever()
