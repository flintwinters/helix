// CodeMirror 5 mode for Helix YAML source: the YAML subset of LANGUAGE.md plus
// Helix structure (!call heads, fn parameters, let bindings, !ref names, !literal data).
// Registers as `helix` when CodeMirror is loaded; under Node it exports the mode factory.
(function () {
  'use strict';
  const FLOW = /[,\[\]{}]/;
  const SPACE = /\s/;
  const STANDARD_TAGS = new Set(['!!str', '!!int', '!!float', '!!bool', '!!null', '!!seq', '!!map']);
  const HELIX_TAGS = {'!call': 'call', '!ref': 'ref', '!literal': 'literal'};
  const RESERVED_KEYS = new Set(['$call', '$ref', '$literal']);

  // Plain scalar values as yaml-rust2 resolves them; non-finite floats are rejected by Helix.
  function plainValueStyle(text) {
    if (/^(?:null|Null|NULL|~)$/.test(text)) return 'atom';
    if (/^(?:true|True|TRUE|false|False|FALSE)$/.test(text)) return 'atom';
    if (/^[-+]?(?:0x[0-9a-fA-F]+|0o[0-7]+|[0-9]+)$/.test(text)) return 'number';
    if (/^[-+]?(?:\.[0-9]+|[0-9]+(?:\.[0-9]*)?)(?:[eE][-+]?[0-9]+)?$/.test(text)) return 'number';
    if (/^[-+]?\.(?:inf|Inf|INF)$|^\.(?:nan|NaN|NAN)$/.test(text)) return 'error';
    return 'string';
  }

  function helixMode(config, options) {
    const builtins = (options && options.builtins) || {};

    function actorStyle(name) {
      const family = Object.prototype.hasOwnProperty.call(builtins, name) ? builtins[name] : null;
      if (family === 'forms') return 'keyword helix-form';
      return family ? 'builtin' : 'helix-actor';
    }

    function top(state) { return state.stack[state.stack.length - 1]; }
    function inFlow(state) { const ctx = top(state); return !!ctx && ctx.flow; }

    // Properties of the next element of a sequence: call heads, fn parameters, let bindings.
    function itemProps(ctx) {
      if (ctx.literal) return {literal: true};
      if (ctx.call) {
        if (ctx.index === 0) return {role: 'head'};
        if (ctx.index === 1 && ctx.head === 'fn') return {role: 'params'};
        if (ctx.index === 1 && ctx.head === 'let') return {role: 'bindings'};
        return {};
      }
      if (ctx.role === 'params') return {role: 'param'};
      if (ctx.role === 'pair' && ctx.index === 0) return {role: 'param'};
      return {};
    }

    function push(state, kind, flow, indent, props) {
      const literal = !!(props.literal || props.tag === 'literal');
      let role = literal ? null : props.role || null;
      if (role === 'param' && kind === 'seq') role = 'pair';
      const ctx = {kind, flow, indent, index: 0, head: null, role,
                   call: !literal && props.tag === 'call' && kind === 'seq', literal};
      state.stack.push(ctx);
      state.pending = kind === 'seq' && flow ? itemProps(ctx) : {};
      return ctx;
    }

    function take(state) { const props = state.pending; state.pending = {}; state.valueOpen = false; return props; }

    // A key starts a block mapping entry or continues the enclosing mapping.
    function keyStyle(state, text, column) {
      let ctx = top(state);
      if (!ctx || (!ctx.flow && !(ctx.kind === 'map' && ctx.indent === column)))
        ctx = push(state, 'map', false, column, take(state));
      state.pending = {};
      state.valueOpen = false;
      state.owner = column;
      if (ctx.literal) return 'property';
      if (RESERVED_KEYS.has(text)) return 'error';  // write !call/!ref/!literal tags instead
      return ctx.role === 'bindings' ? 'def' : 'property';
    }

    function nodeStyle(state, base, text) {
      const props = take(state);
      const ctx = top(state);
      if (props.role === 'head') {
        if (ctx) ctx.head = props.tag ? null : text;
        return props.tag === 'ref' ? 'variable-2 helix-ref' : actorStyle(text);
      }
      if (props.tag === 'ref') return 'variable-2 helix-ref';
      if (props.role === 'param') return 'def';
      return props.string ? 'string' : base;
    }

    // Plain scalar extent: stops at `: `, ` #` and, inside flow collections, flow indicators.
    function plainEnd(stream, flow) {
      const s = stream.string;
      let pos = stream.pos;
      while (pos < s.length) {
        const c = s[pos], next = s[pos + 1];
        if (c === ':' && (next === undefined || SPACE.test(next) || (flow && FLOW.test(next)))) break;
        if (c === '#' && SPACE.test(s[pos - 1])) break;
        if (flow && FLOW.test(c)) break;
        pos++;
      }
      while (pos > stream.pos && SPACE.test(s[pos - 1])) pos--;
      return pos;
    }

    function isKeyAhead(stream, flow) {
      return stream.match(flow ? /^\s*:(?=\s|$|[,\[\]{}])/ : /^\s*:(?=\s|$)/, false);
    }

    function quoted(stream, state) {
      const quote = state.quote;
      while (!stream.eol()) {
        const c = stream.next();
        if (quote === '"' && c === '\\') stream.next();
        else if (c === quote) {
          if (quote === "'" && stream.eat("'")) continue;
          state.quote = null;
          break;
        }
      }
      return state.quoteStyle;
    }

    // Block structure: close mappings and sequences that the indentation of this line leaves.
    function closeBlocks(state, stream, column) {
      const dash = stream.match(/^-(?=\s|$)/, false);
      while (state.stack.length) {
        const ctx = top(state);
        if (ctx.flow || ctx.indent < column) break;
        if (ctx.indent === column && ctx.kind === 'seq' && dash) break;
        if (ctx.indent === column && ctx.kind === 'map' && (!dash || state.valueOpen)) break;
        state.stack.pop();
      }
    }

    return {
      startState() {
        return {stack: [], pending: {}, quote: null, quoteStyle: null, blockScalar: null,
                owner: 0, lineFirst: true, valueOpen: false};
      },

      copyState(state) {
        return Object.assign({}, state, {stack: state.stack.map(ctx => Object.assign({}, ctx)),
                                         pending: Object.assign({}, state.pending)});
      },

      token(stream, state) {
        if (stream.sol()) {
          state.lineFirst = true;
          if (state.blockScalar !== null) {
            if (stream.match(/^\s*$/) || stream.indentation() > state.blockScalar) {
              stream.skipToEnd();
              return 'string';
            }
            state.blockScalar = null;
          }
        }
        if (state.quote) { state.lineFirst = false; return quoted(stream, state); }
        if (stream.eatSpace()) return null;
        const ch = stream.peek();
        if (ch === '#') { stream.skipToEnd(); return 'comment'; }

        const column = stream.column();
        if (state.lineFirst) {
          state.lineFirst = false;
          if (column === 0 && stream.match(/^(?:---|\.\.\.)(?=\s|$)/)) {
            state.stack = []; state.pending = {};
            return 'meta';
          }
          if (column === 0 && ch === '%') { stream.skipToEnd(); return 'meta'; }
          if (!inFlow(state)) closeBlocks(state, stream, column);
        }
        const flow = inFlow(state);

        if (!flow && stream.match(/^-(?=\s|$)/)) {
          let ctx = top(state);
          if (ctx && ctx.kind === 'seq' && ctx.indent === column) ctx.index++;
          else ctx = push(state, 'seq', false, column, take(state));
          state.pending = itemProps(ctx);
          state.owner = column;
          return 'meta helix-indicator';
        }
        if ((ch === '?' || ch === ':') && stream.match(/^[?:](?=\s|$|[,\[\]{}])/)) {
          if (ch === ':') {
            const ctx = top(state);
            state.pending = ctx && ctx.literal ? {literal: true} : {};
            state.valueOpen = true;
          }
          return 'meta helix-indicator';
        }
        if (ch === '[' || ch === '{') {
          stream.next();
          push(state, ch === '[' ? 'seq' : 'map', true, column, take(state));
          return 'bracket';
        }
        if (ch === ']' || ch === '}') {
          stream.next();
          if (flow) state.stack.pop();
          return flow ? 'bracket' : 'error';
        }
        if (ch === ',' && flow) {
          stream.next();
          const ctx = top(state);
          ctx.index++;
          state.pending = ctx.kind === 'seq' ? itemProps(ctx) : {};
          return 'meta helix-indicator';
        }
        if (ch === '!') {
          const tag = stream.match(/^!(?:<[^>]*>|!?[^\s,\[\]{}]*)/)[0];
          if (HELIX_TAGS[tag]) {
            const kind = HELIX_TAGS[tag];
            if (!state.pending.literal) state.pending = Object.assign({}, state.pending, {tag: kind});
            return 'helix-tag helix-tag-' + kind;
          }
          if (!STANDARD_TAGS.has(tag)) return 'error';
          if (tag === '!!str') state.pending = Object.assign({}, state.pending, {string: true});
          return 'meta';
        }
        if (ch === '&') { stream.match(/^&[^\s,\[\]{}]*/); return 'helix-anchor'; }
        if (ch === '*') { stream.match(/^\*[^\s,\[\]{}]*/); take(state); return 'helix-anchor'; }
        if (!flow && (ch === '|' || ch === '>') && stream.match(/^[|>][0-9+-]*(?=\s|$)/)) {
          take(state);
          state.blockScalar = state.owner;
          return 'meta helix-indicator';
        }
        if (ch === '"' || ch === "'") {
          const single = stream.match(ch === '"' ? /^"(?:[^"\\]|\\.)*"/ : /^'(?:[^']|'')*'/);
          if (single && isKeyAhead(stream, flow)) {
            const text = single[0].slice(1, -1);
            return 'string ' + keyStyle(state, text, column);
          }
          const style = nodeStyle(state, 'string', single ? single[0].slice(1, -1) : null);
          if (single) return style;
          stream.next();
          state.quote = ch;
          state.quoteStyle = style;
          return quoted(stream, state);
        }

        const end = plainEnd(stream, flow);
        if (end === stream.pos) { stream.next(); return 'error'; }
        const text = stream.string.slice(stream.pos, end);
        stream.pos = end;
        if (isKeyAhead(stream, flow)) return keyStyle(state, text, column);
        return nodeStyle(state, plainValueStyle(text), text);
      },

      lineComment: '#',
      fold: 'indent',
    };
  }

  if (typeof CodeMirror !== 'undefined') {
    CodeMirror.defineMode('helix', helixMode);
    CodeMirror.defineMIME('text/x-helix', 'helix');
  }
  if (typeof module === 'object' && module.exports) module.exports = helixMode;
})();
