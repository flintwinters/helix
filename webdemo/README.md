# Helix web demo

This is a minimal HTTP-backed message handler written in Helix YAML. The
isolated `web.so` native include owns HTTP transport and framing; `server.yaml`
owns a pure Helix function that performs exactly one `message -> response`
turn for each request.

`web.serve` takes its server options followed by a Helix function:

```yaml
response:
  status: 200
  content_type: text/plain; charset=utf-8
  body: Hello
handler:
  type: function
  params: [message]
  body:
    - [return, response]
main:
  - web.serve
  - {port: 8080, max_requests: 0}
  - handler
```

The function receives an HTTP request map with `method`, `path`, `content_type`,
and `body`. It returns a map containing `body` plus optional `status` (default
`200`) and `content_type` (default `application/yaml; charset=utf-8`). An
`application/yaml` body may be any Helix/YAML mapping, sequence, integer,
string, or null. Other media types carry raw string bodies.

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

Verify structured YAML, raw text, empty-body, and malformed-body turns with:

```bash
uv run python manage.py webdemo-test
```
