// Workbook mode: lessons from /workbook.json, grading by POST /grade (the Helix binary decides every verdict).
// Needs the page globals `editor`, `output` and `helixMode()` from index.html.
const Workbook = (() => {
  const KEY = 'helix-workbook';
  let flat = [], current = null, state = {done: {}, drafts: {}, at: null}, active = false;
  const pane = () => document.getElementById('lesson');

  // Browser storage is a per-viewer convenience: every access tolerates it being absent.
  const load = () => { try { Object.assign(state, JSON.parse(localStorage.getItem(KEY)) || {}); } catch (e) {} };
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) {} };

  const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

  // Inline markdown subset: `code`, **bold**, *emphasis*. Built from DOM nodes, never from HTML strings.
  function inline(text) {
    const out = document.createDocumentFragment();
    for (const part of text.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*)/)) {
      if (part.startsWith('`')) out.append(el('code', '', part.slice(1, -1)));
      else if (part.startsWith('**')) out.append(el('strong', '', part.slice(2, -2)));
      else if (part.startsWith('*') && part.length > 2) out.append(el('em', '', part.slice(1, -1)));
      else out.append(part);
    }
    return out;
  }

  function fence(kind, code) {
    const box = el('div', 'fence ' + kind);
    const view = CodeMirror(box, {value: code.replace(/\n$/, ''), mode: helixMode(), theme: 'material-darker',
      readOnly: 'nocursor', viewportMargin: Infinity});
    if (kind !== 'text') {
      const open = el('button', 'tiny', kind === 'helix' ? 'Open in editor' : 'Try this failing example');
      open.onclick = () => { editor.setValue(code); output.setValue(''); };
      box.append(open);
    }
    setTimeout(() => view.refresh(), 0);
    return box;
  }

  // Block markdown subset: fenced code, "- " lists, paragraphs.
  function render(markdown) {
    const root = document.createDocumentFragment();
    const lines = markdown.split('\n');
    for (let i = 0; i < lines.length;) {
      const open = lines[i].match(/^```(\S*)$/);
      if (open) {
        const body = [];
        for (i++; i < lines.length && !lines[i].startsWith('```'); i++) body.push(lines[i]);
        i++;
        root.append(fence(open[1] || 'text', body.join('\n') + '\n'));
      } else if (/^- /.test(lines[i])) {
        const list = el('ul');
        for (; i < lines.length && /^- /.test(lines[i]); i++) { const li = el('li'); li.append(inline(lines[i].slice(2))); list.append(li); }
        root.append(list);
      } else if (lines[i].trim() === '') i++;
      else {
        const para = [];
        for (; i < lines.length && lines[i].trim() !== '' && !/^```|^- /.test(lines[i]); i++) para.push(lines[i]);
        const p = el('p'); p.append(inline(para.join(' '))); root.append(p);
      }
    }
    return root;
  }

  const stepAt = i => flat[Math.max(0, Math.min(flat.length - 1, i))];
  const indexOf = id => flat.findIndex(s => s.id === id);
  const program = source => current && current.probe ? source.replace(/\n*$/, '\n') + current.probe : source;

  function toc() {
    const nav = el('details', 'toc'); nav.append(el('summary', '', `Contents · ${Object.keys(state.done).filter(id => indexOf(id) >= 0).length}/${flat.length} done`));
    let chapter = null;
    for (const s of flat) {
      if (s.chapter !== chapter) { chapter = s.chapter; nav.append(el('div', 'toc-chapter', s.chapterTitle)); }
      const a = el('a', s.id === current.id ? 'on' : '', (state.done[s.id] ? '✓ ' : '· ') + s.title);
      a.onclick = () => show(s.id);
      nav.append(a);
    }
    return nav;
  }

  async function show(id) {
    if (current) state.drafts[current.id] = editor.getValue();
    current = flat[Math.max(0, indexOf(id))]; state.at = current.id; save();
    try { history.replaceState(null, '', '#' + current.id); } catch (e) {}
    const i = indexOf(current.id), p = pane(); p.replaceChildren();
    p.append(toc());
    p.append(el('div', 'crumb', `${current.chapterTitle} · step ${i + 1} of ${flat.length}`));
    p.append(el('h2', '', current.title));
    p.append(render(current.teach));
    const task = el('div', 'task'); task.append(el('b', '', 'Your turn. ')); task.append(inline(current.task || 'Run it and read the result.'));
    if (current.probe) { task.append(el('div', 'probe-note', 'The checker appends this to your program:')); task.append(fence('text', current.probe)); }
    p.append(task);
    const verdict = el('div', 'verdict'); verdict.id = 'verdict'; p.append(verdict);
    const row = el('div', 'row');
    const check = el('button', 'primary', 'Check answer'); check.onclick = grade;
    const hint = el('button', '', 'Hint'); hint.onclick = () => { verdict.className = 'verdict hint'; verdict.textContent = current.hint || 'No hint for this step.'; };
    const solution = el('button', '', 'Show solution'); solution.hidden = !current.has_solution; solution.onclick = reveal;
    const reset = el('button', '', 'Reset'); reset.onclick = () => { editor.setValue(current.start); output.setValue(''); };
    row.append(check, hint, solution, reset); p.append(row);
    const nav = el('div', 'row');
    const prev = el('button', '', '← Previous'); prev.disabled = i === 0; prev.onclick = () => show(stepAt(i - 1).id);
    const next = el('button', '', 'Next →'); next.disabled = i === flat.length - 1; next.onclick = () => show(stepAt(i + 1).id);
    nav.append(prev, next); p.append(nav);
    editor.setValue(state.drafts[current.id] ?? current.start); output.setValue('');
    p.scrollTop = 0;
  }

  async function grade() {
    const verdict = document.getElementById('verdict'); verdict.className = 'verdict'; verdict.textContent = 'checking…';
    try {
      const reply = await fetch('/grade?id=' + encodeURIComponent(current.id), {method: 'POST', body: editor.getValue()});
      const result = await reply.json();
      output.setValue(result.output || ''); showError(result.output || '');
      verdict.className = 'verdict ' + (result.passed ? 'pass' : 'fail');
      verdict.textContent = (result.passed ? '✓ ' : '✗ ') + result.message;
      if (result.passed) { state.done[current.id] = true; save(); }
    } catch (e) { verdict.className = 'verdict fail'; verdict.textContent = 'could not grade: ' + e; }
  }

  async function reveal() {
    if (!confirm('Show the reference solution? Try the hint first.')) return;
    const text = await (await fetch('/solution?id=' + encodeURIComponent(current.id))).text();
    editor.setValue(text);
  }

  return {
    get active() { return active; },
    program,
    async enter() {
      active = true; load(); pane().hidden = false;
      if (!flat.length) {
        const reply = await fetch('/workbook.json');
        if (!reply.ok) { pane().textContent = await reply.text(); return; }
        for (const c of await reply.json()) for (const s of c.steps) flat.push({...s, chapter: c.file, chapterTitle: c.title});
      }
      await show(location.hash.slice(1) || state.at || flat[0].id);
    },
    leave() { active = false; pane().hidden = true; if (current) { state.drafts[current.id] = editor.getValue(); save(); } current = null; },
    onEdit() { if (current) { state.drafts[current.id] = editor.getValue(); save(); } },
  };
})();
