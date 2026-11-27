#!/usr/bin/env python3
"""Build review.html: a self-contained, offline browser for every agent-bench run here.

Rerun after new runs: ./manage.py quality agent-bench review
Files are deduplicated by content and embedded gzip+base64; the page inflates
them lazily with the browser's DecompressionStream, so it works from file://.
Helix YAML (files and sources embedded in transcripts) is canonicalized by one
`./manage.py runtime format --write` call; unparseable sources stay verbatim.
"""
import base64, gzip, hashlib, json, pathlib, subprocess, tempfile

import yaml

REPO = pathlib.Path(__file__).resolve().parent.parent
ROOT = REPO / 'runtime' / 'agent-bench'
OUT = ROOT / 'review.html'
SKIP = {OUT.name, 'build_review.py', 'review_template.html'}


def load(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def embedded_sources(conversation):
    """YAML a model wrote inline: written .yaml files, stdin programs and chat answers."""
    for message in conversation:
        if message.get('role') == 'assistant' and isinstance(message.get('content'), str):
            yield message['content']
        for call in message.get('tool_calls') or []:
            try:
                args = json.loads(call['function']['arguments'])
            except (KeyError, TypeError, ValueError):
                continue
            if isinstance(args, dict):
                if str(args.get('path', '')).endswith('.yaml') and isinstance(args.get('content'), str):
                    yield args['content']
                if isinstance(args.get('stdin'), str):
                    yield args['stdin']


def canonicalize(sources):
    """Map each source text to its canonical Helix formatting, when it parses and differs."""
    with tempfile.TemporaryDirectory(prefix='.review-fmt-', dir=ROOT) as directory:
        stage = pathlib.Path(directory)
        staged = {}
        for text in sources:
            path = stage / (hashlib.sha256(text.encode()).hexdigest()[:20] + '.yaml')
            path.write_text(text)
            staged[text] = path
        run = subprocess.run([str(REPO / 'manage.py'), 'runtime', 'format', '--write', str(stage)],
                             cwd=REPO, capture_output=True, text=True)
        unparsed = run.stdout.count('error: ') + run.stderr.count('error: ')
        formatted = {text: path.read_text() for text, path in staged.items()}
    print(f'{len(staged)} YAML sources: {unparsed} unparseable (shown verbatim)')
    return {text: out for text, out in formatted.items() if out != text}


def row_index(row):
    score = row.get('score') or {}
    cases = score.get('cases') or []
    failed = next((c for c in cases if not c.get('passed')), None)
    failure = None
    if failed:
        obs = failed.get('observation') or {}
        err = (obs.get('state') or {}).get('error') or {}
        failure = {'case': failed.get('id'), 'kind': err.get('kind'),
                   'message': err.get('message') or obs.get('malformed') or (obs.get('stderr') or '')[:300]}
    usage = row.get('usage') or {}
    keys = ('model_requests', 'tool_calls', 'cli_calls', 'public_test_calls', 'public_test_successes',
            'generation_ms', 'runtime_ratio_to_reference', 'error', 'source_bytes')
    return {'artifact': row['artifact'], 'task': row['task'], 'trial': row['trial'],
            'passed': bool(score.get('passed')) if score else None,  # None: not scored yet
            'cases_passed': score.get('cases_passed', sum(1 for c in cases if c.get('passed'))),
            'cases_total': score.get('cases_total', len(cases)),
            'tokens': usage.get('total_tokens'), 'cost': usage.get('cost'),
            'failure': failure, **{k: row.get(k) for k in keys}}


def read_report(run, name):
    path = run / (name + '.yaml')
    if path.is_file():
        return yaml.load(path.read_text(), Loader=getattr(yaml, 'CSafeLoader', yaml.SafeLoader))
    return load(run / (name + '.json'))


def run_index(run):
    manifest = read_report(run, 'manifest') or {}
    report = read_report(run, 'report') or {}
    replay = read_report(run, 'replay')
    if replay:  # rescored by `agent-bench score` (after an interruption or under other limits): the latest evaluation wins
        timeout = (replay.get('evaluation') or {}).get('test_timeout_seconds')
        report = {**report, 'phase': f"{report.get('phase')}; rescored by replay" + (f' ({timeout:g}s timeout)' if timeout else ''),
                  'rows': replay.get('rows'), 'summary': replay.get('summary')}
    tasks = read_report(run, 'tasks') or {}
    keep = ('model', 'mode', 'protocol_version', 'task_version', 'trials', 'samples', 'temperature',
            'max_tokens', 'max_turns', 'max_tool_calls', 'jobs', 'context_delivery', 'git_revision', 'dry_run', 'retry_of', 'test_timeout_seconds')
    return {'manifest': {k: manifest[k] for k in keep if k in manifest},
            'execution': report.get('execution'), 'phase': report.get('phase'),
            'summary': report.get('summary') or {},
            'tasks': {name: {'goal': t.get('goal'), 'example': t.get('example')} for name, t in tasks.items()},
            'rows': [row_index(r) for r in report.get('rows') or []]}


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    blobs, files, file_sources, inline = {}, [], {}, set()
    for path in sorted(ROOT.rglob('*')):
        rel = path.relative_to(ROOT)
        if not path.is_file() or path.name in SKIP or path.name.endswith('.matrix.js') or any(p.startswith('.') for p in rel.parts):
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()[:16]
        if digest not in blobs:
            blobs[digest] = base64.b64encode(gzip.compress(data, 9, mtime=0)).decode()
            if path.suffix == '.yaml':
                file_sources[digest] = data.decode(errors='replace')
            elif path.name == 'conversation.json':
                inline.update(embedded_sources(load(path) or []))
        files.append([rel.as_posix(), len(data), digest])
    formatted = canonicalize(set(file_sources.values()) | inline)
    runs = {p.name: run_index(p) for p in sorted(ROOT.iterdir()) if p.is_dir() and not p.name.startswith('.')}
    payload = {'files': files, 'runs': runs, 'blobs': blobs,
               'formattedFiles': {h: formatted[t] for h, t in file_sources.items() if t in formatted},
               'formattedInline': {t: formatted[t] for t in inline if t in formatted}}
    data = json.dumps(payload, separators=(',', ':')).replace('</', '<\\/')
    template = pathlib.Path(__file__).with_name('agent_review.html').read_text()
    OUT.write_text(template.replace('/*DATA*/null', data))
    print(f'{OUT}: {len(files)} files, {len(blobs)} unique, {len(formatted)} sources formatted, '
          f'{OUT.stat().st_size / 1e6:.1f} MB')


if __name__ == '__main__':
    main()
