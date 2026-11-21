# Editor integration

Start `helix lsp` as a stdio language server for YAML Helix buffers. The server
implements LSP 3.17 initialization/shutdown, full text synchronization, published
parse/static arity diagnostics, and whole-document canonical formatting. Editors
must send `didOpen` followed by full `didChange` contents with increasing versions.
Positions use UTF-16. Diagnostics inspect uncalled bodies and lazy branches without
executing expressions or opening imports. Unsupported requests receive MethodNotFound.

Associate `editor/helix.schema.json` with your Helix YAML files in your YAML
extension, or obtain the installed copy using `helix schema`. The schema describes
the recursive program shape, explicit singleton call/reference forms and literal data boundaries. Scalar quoting has no execution meaning. JSON Schema cannot determine dynamic Helix types. The language server
supplies source-aware diagnostics; evaluation errors remain `helix run` or
`helix check --evaluate` responsibilities.

`helix fmt file.yaml` writes canonical source to stdout. Formatting keeps
comments, re-attached by structural path, and normalizes scalar presentation while preserving explicit syntax and scalar values. Mapping presentation order has no execution meaning. The formatter reparses its output and checks exact
source-graph equivalence before returning it. It never evaluates a program.

Protocol reference: https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/

`editor/web/helix-mode.js` is the browser editor's CodeMirror mode. It styles Helix
structure (`!call` heads by builtin family, `fn` parameters and `let` names as definitions,
`!ref` names, `!literal` payloads as data) and flags forms Helix rejects as written YAML
(`{$call: ...}` maps outside `!literal`, unknown tags, `.inf`/`.nan`). Builtin names come
from `helix builtins --json` via `serve.py`. `./manage.py test --suite editor` tokenizes
through Node with `editor/web/tokens.js`.

The browser editor's sidebar lists every `examples/**/*.yaml` and `std/**/*.yaml` file
(`serve.py` serves only those listed paths) and loads the chosen one into the buffer;
editing it never writes back to the repository.

The browser editor opens in **Workbook** mode: a step-by-step course for experienced programmers,
with lessons as YAML under `editor/web/workbook/` (id, prose with ` ```helix ` fences, task, starter,
solution, hint, expected value and an optional `probe` the checker appends). `Check answer` posts the
buffer to `serve.py`, which runs it and lets Helix itself compare the value with the expectation
(`editor/web/workbook.py`; needs PyYAML). Solutions stay server-side until requested. Progress and
drafts live in browser storage only. **Sandbox** mode is the free buffer with the library sidebar.
`./manage.py test --suite workbook` proves every solution passes, every starter fails its task and
every prose example runs (or fails, for ` ```helix-error `) as labelled.
