# Helix web demo

This is a minimal HTTP server whose configuration and endpoint objects are
written in Helix YAML. The isolated `web.so` native include supplies only the
loopback socket boundary; `server.yaml` owns the routes and their page bodies.

`web.serve` takes its server options followed directly by its endpoint array:

```yaml
main:
  - web.serve
  - {port: 8080, max_requests: 0}
  - - {path: /, body: Home page}
    - {path: /health, body: ok}
```

Each endpoint needs a unique slash-prefixed `path` and a string `body`. A path
without an endpoint returns `404 Not Found`.

Build it through the repository entrypoint:

```bash
uv run python manage.py webdemo
```

Then run the Helix program and visit <http://127.0.0.1:8080>:

```bash
./build/helix webdemo/server.yaml
```

The listener is deliberately restricted to `127.0.0.1`. Set `max_requests` to
zero for a long-running server, or to a positive number for a bounded run.

Verify one real HTTP request with:

```bash
uv run python manage.py webdemo-test
```
