# Editor integration

Start `helix lsp` as a stdio language server for YAML Helix buffers. The server
implements LSP 3.17 initialization/shutdown, full text synchronization, published
parse/static arity diagnostics, and whole-document canonical formatting. Editors
must send `didOpen` followed by full `didChange` contents with increasing versions.
Positions use UTF-16. Diagnostics inspect uncalled bodies and lazy branches without
executing expressions or opening imports. Unsupported requests receive MethodNotFound.

Associate `editor/helix.schema.json` with your Helix YAML files in your YAML
extension, or obtain the installed copy using `helix schema`. The schema describes
the recursive program shape. JSON Schema cannot distinguish quoted YAML heads
from executable vectors or determine dynamic Helix types. The language server
supplies source-aware diagnostics; evaluation errors remain `helix run` or
`helix check --evaluate` responsibilities.

`helix fmt file.yaml` writes canonical source to stdout. Formatting removes
comments and normalizes scalar presentation while retaining map order, symbolic
strings and quoted literals. The formatter reparses its output and checks exact
source-graph equivalence before returning it. It never evaluates a program.

Protocol reference: https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/
