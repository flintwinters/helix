"""Browser editor highlighting: Helix structure styles from editor/web/helix-mode.js under Node; run via manage.py test."""
import json
import shutil
import subprocess

SOURCE = """\
# comment
all: !call
  - fn
  - [[xs, !ref list], fallback]
  - !call [find_index, !ref xs.items, "two words", -7, 0x1F, 1.5e3, ~, true]
bindings: !call
  - let
  - a: 1
    b: !call [f, !ref a]
  - !ref a
data: !literal {$call: [add, 1]}
written: {$call: [add, 1]}
quoted: "multi
  line" # trailing
block: |
  key: inside
  - item
after: !!str 1
unknown: !foo x
bad: [.inf, &a x, *a]
"""


def tokens(ctx, source, builtins):
    node = shutil.which("node")
    assert node, "the editor suite needs Node.js (`node`) on PATH to tokenize editor/web/helix-mode.js"
    done = subprocess.run([node, str(ctx.root / "editor" / "web" / "tokens.js")], capture_output=True, text=True,
                          input=json.dumps({"source": source, "builtins": builtins}), timeout=ctx.timeout)
    assert done.returncode == 0, done.stderr
    return [tuple(token) for token in json.loads(done.stdout)]


def check(ctx):
    listed = ctx.helix("builtins", "--json")
    assert listed.returncode == 0, listed
    builtins = {row["name"]: row["family"] for row in json.loads(listed.stdout)}
    seen = tokens(ctx, SOURCE, builtins)
    expected = [
        (1, "# comment", "comment"), (2, "all", "property"), (2, "!call", "helix-tag helix-tag-call"),
        # Call heads: forms, other builtins and user functions differ; fn parameters and let names are definitions.
        (3, "fn", "keyword helix-form"), (4, "xs", "def"), (4, "list", "variable-2 helix-ref"), (4, "fallback", "def"),
        (5, "find_index", "helix-actor"), (5, "xs.items", "variable-2 helix-ref"), (5, '"two words"', "string"),
        (5, "-7", "number"), (5, "0x1F", "number"), (5, "1.5e3", "number"), (5, "~", "atom"), (5, "true", "atom"),
        (7, "let", "keyword helix-form"), (8, "a", "def"), (9, "b", "def"), (9, "f", "helix-actor"),
        # Reserved keys are data inside !literal and rejected as written YAML elsewhere.
        (11, "!literal", "helix-tag helix-tag-literal"), (11, "$call", "property"), (11, "add", "string"),
        (12, "$call", "error"), (12, "add", "string"),
        (13, '"multi', "string"), (14, 'line"', "string"), (14, "# trailing", "comment"),
        (16, "key: inside", "string"), (17, "- item", "string"), (18, "after", "property"),
        (18, "!!str", "meta"), (18, "1", "string"), (19, "!foo", "error"),
        (20, ".inf", "error"), (20, "&a", "helix-anchor"), (20, "*a", "helix-anchor"),
    ]
    # Whitespace-trimmed text keeps the expectations independent of indentation inside tokens.
    actual = {(line, text.strip(), style) for line, text, style in seen}
    missing = [token for token in expected if token not in actual]
    assert not missing, f"missing tokens {missing}\nactual {seen}"

    # Without the builtin catalog every head is still an actor, never plain data.
    assert (3, "fn", "helix-actor") in set(tokens(ctx, SOURCE, {}))
    # Every repository source tokenizes without stalling the mode.
    for path in sorted((ctx.root / "std").glob("*.yaml")):
        tokens(ctx, path.read_text(), builtins)
    return []
