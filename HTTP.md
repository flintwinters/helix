# Native HTTP servers

`std:http` runs whole-body HTTP/1.1 handlers inside ordinary `perform` execution.
Transport is Rust; handlers are captured Helix functions. TLS, streaming,
WebSockets and restoration of a live listener or client connection are unsupported.

```yaml
http: {$call: [import, 'std:http']}
main:
  $call:
  - http.serve
  - {bind: '127.0.0.1:8080'}
  - {$call: [fn, [request], {$call: [http.text, 200, Hello]}]}
```

Run with `python manage.py perform --allow-listen 127.0.0.1:8080 program.yaml`.
A listener grant is an exact numeric loopback socket address. Port zero must be
explicitly granted as `127.0.0.1:0`; readiness metadata reports the chosen port.
Outbound HTTP grants do not authorize listening. Each connection serves one request.

`http.serve(options, handler)` accepts `bind`, optional positive `requests`, named
`actors` and a handler `promise`. It returns listener address and summary counts
after the request limit or cancellation. `requests` counts parsed service requests,
including requests rejected for capacity or body bounds. Malformed transport input
may close before reaching the service. Cancelling stops admission and settles
started work. A request deadline does not cancel an already started blocking effect.

Requests contain `method`, raw `target`, `path`, raw `query`, ordered `headers`
pairs and a UTF-8 `body`. Header names are normalized by the transport; duplicate
values survive. Header pairs are not a byte-for-byte wire reconstruction.

`http.respond(status, headers, body)` creates a text response;
`http.text(status, body)` sets a plain UTF-8 content type, and
`http.json_response(status, value)` uses `std:json.dump`. `http.validate(value)`
checks the language response shape; the transport additionally checks status,
header syntax and caller-owned size bounds. Status must be 200 through 599.
Framing headers are transport-owned. HEAD returns the corresponding body length
without transmitting the body. Handler errors produce a bounded 500 response;
Helix errors never become response bodies. Capacity exhaustion returns 503 and
request deadlines return 504 when a protocol response is still possible.

`http.route(routes, fallback)` returns a handler selecting the first exact
`{method, path, handler}` match. Both method and path are case-sensitive. Every
router has an explicit fallback; routes do not perform prefix matching.

The caller owns ceilings: 32 connections, 8 handlers, 8 actors, 64 queued messages
per actor, 16 KiB headers, 256 KiB dynamic bodies, 1 MiB static files and 10 seconds
per request by default. Handler machines have independent existing execution
budgets. Completed machines and their histories are retired; the server retains
active work and summary counters. Worker code cannot start another server or
perform process-wide stdin/exit operations.

`--server-progress NEW_FILE` writes versioned JSONL readiness and shutdown metadata,
including the ephemeral address and failure counts. The file must not exist.
Metadata omits request bodies. Program stdout and full-state stderr retain their
usual `perform` contracts. Journaled server startup is rejected before binding;
actor data persistence does not restore active requests, mailboxes or connections.

Promises and grants remain independent. The parent VM's effect restrictions apply
to workers, and a handler promise can narrow them. `{effects: []}` forbids effects;
composed effect lists intersect. A reversible handler can compute a pure response;
transport sends it outside that machine. Ordinary host dispatch from reversible
machines remains forbidden.
