// Tokenize Helix source with helix-mode.js outside a browser, as CodeMirror's runMode does.
// stdin: {"source": "...", "builtins": {"name": "family", ...}}
// stdout: [[line, text, style], ...] for every non-whitespace token (style null when unstyled).
'use strict';
const helixMode = require('./helix-mode.js');

// The subset of CodeMirror 5's StringStream that the mode uses, with the same semantics.
class StringStream {
  constructor(string) { this.string = string; this.pos = this.start = 0; }
  sol() { return this.pos === 0; }
  eol() { return this.pos >= this.string.length; }
  peek() { return this.string.charAt(this.pos) || undefined; }
  next() { if (this.pos < this.string.length) return this.string.charAt(this.pos++); }
  eat(ch) { if (this.string.charAt(this.pos) === ch) { this.pos++; return ch; } }
  eatSpace() { const start = this.pos; while (/\s/.test(this.string.charAt(this.pos))) this.pos++; return this.pos > start; }
  skipToEnd() { this.pos = this.string.length; }
  column() { return this.start; }
  indentation() { return this.string.search(/\S|$/); }
  current() { return this.string.slice(this.start, this.pos); }
  match(pattern, consume) {
    const match = this.string.slice(this.pos).match(pattern);
    if (!match || match.index > 0) return null;
    if (consume !== false) this.pos += match[0].length;
    return match;
  }
}

const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const mode = helixMode({}, {builtins: input.builtins || {}});
let state = mode.startState();
const tokens = [];
input.source.split('\n').forEach((line, index) => {
  const stream = new StringStream(line);
  while (!stream.eol()) {
    const style = mode.token(stream, state);
    if (stream.pos <= stream.start) throw new Error(`mode did not advance on line ${index + 1}: ${line}`);
    if (stream.current().trim()) tokens.push([index + 1, stream.current(), style || null]);
    stream.start = stream.pos;
  }
  state = mode.copyState(state);
});
process.stdout.write(JSON.stringify(tokens) + '\n');
