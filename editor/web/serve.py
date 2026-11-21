"""Serves index.html and runs the helix CLI on posted source. Usage: python3 editor/web/serve.py [port]"""
import http.server, json, pathlib, subprocess, sys, urllib.parse
HERE = pathlib.Path(__file__).parent
BIN = HERE.parents[1] / 'target' / 'debug' / 'helix'
ROOT = HERE.parents[1]
LIBRARIES = ('examples', 'std')  # the only trees the browser may list and read

def library_files():
    return [{'group': top, 'path': f.relative_to(ROOT).as_posix()}
            for top in LIBRARIES for f in sorted((ROOT / top).rglob('*.yaml'))]

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
        if path == '/helix-mode.js':
            return self.reply((HERE / 'helix-mode.js').read_bytes(), kind='text/javascript')
        self.reply((HERE / 'index.html').read_bytes(), kind='text/html')
    def do_POST(self):
        command = COMMANDS.get(urllib.parse.urlparse(self.path).path.strip('/'))
        if not command: return self.reply(b'unknown command', 404)
        source = self.rfile.read(int(self.headers['Content-Length']))
        try:
            done = subprocess.run([BIN, *command, '-'], input=source, capture_output=True, timeout=30)
            self.reply(done.stdout + done.stderr)
        except subprocess.TimeoutExpired:
            self.reply(b'timed out after 30s')

http.server.ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1]) if len(sys.argv) > 1 else 8765), Handler).serve_forever()
