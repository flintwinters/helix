"""Serves index.html and runs the helix CLI on posted source. Usage: python3 editor/web/serve.py [port]"""
import http.server, pathlib, subprocess, sys, urllib.parse
HERE = pathlib.Path(__file__).parent
BIN = HERE.parents[1] / 'target' / 'debug' / 'helix'
COMMANDS = {'run': ['run'], 'check': ['check', '--evaluate'], 'fmt': ['fmt'], 'step': ['run', '--annotations']}

class Handler(http.server.BaseHTTPRequestHandler):
    def reply(self, body, status=200, kind='text/plain'):
        self.send_response(status); self.send_header('Content-Type', kind + '; charset=utf-8'); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
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
