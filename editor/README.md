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

`helix fmt file.yaml` writes canonical source to stdout. Formatting removes
comments (the browser's Format button re-attaches them with `tooling/source_format.py`) and normalizes scalar presentation while preserving explicit syntax and scalar values. Mapping presentation order has no execution meaning. The formatter reparses its output and checks exact
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
