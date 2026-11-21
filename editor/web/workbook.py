"""Workbook lessons and grading. Lessons are YAML under editor/web/workbook/; the Helix binary decides every verdict.

The lessons build one program, Ada's ledger. A step's `start` and `solution` list only the top-level entries it
adds or changes. `chapters()` merges `start` onto the previous step's solution and `solution` onto that start (so
given helpers need not be repeated), so the learner always continues the program they already wrote.

A runner is `run(args, stdin) -> (returncode, stdout, stderr)`, so the server (subprocess) and the test suite
(`TestContext.helix`) share one implementation. Python only shuttles data: equality is Helix's own `eq`.
"""
import json
import pathlib
import re

LESSONS = pathlib.Path(__file__).parent / 'workbook'
FIELDS = {'id', 'title', 'teach', 'task', 'start', 'solution', 'hint', 'expect', 'probe'}


ENTRY = re.compile(r'^[A-Za-z_][\w.-]*:', re.M)


def entries(source):
    """Top-level map entries of `source` as ordered (name, text) pairs; text before the first entry is dropped."""
    starts = [m.start() for m in ENTRY.finditer(source)]
    spans = zip(starts, starts[1:] + [len(source)])
    return [(source[a:source.index(':', a)], source[a:b].rstrip('\n') + '\n') for a, b in spans]


def merge(previous, delta):
    """`previous` with the entries of `delta` replaced in place or appended. Without entries to merge into
    (a first step, or a scalar program) the delta is the whole program; a delta with no entries is only a comment."""
    base, new = entries(previous or ''), entries(delta)
    if not base: return delta
    if not new: return previous.rstrip('\n') + '\n' + delta
    replaced = dict(new)
    kept = [replaced.pop(name, text) for name, text in base]
    return ''.join(kept + [text for name, text in new if name in replaced])


def chapters():
    """Chapters in filename order, each validated for the step schema (a malformed lesson fails loudly)."""
    import yaml  # lazily: the sandbox editor must start without PyYAML
    result, previous = [], None
    for path in sorted(LESSONS.glob('*.yaml')):
        chapter = yaml.safe_load(path.read_text())
        chapter['file'] = path.name
        for step in chapter['steps']:
            unknown = set(step) - FIELDS
            assert not unknown, f"{path.name}: step {step.get('id')!r} has unknown fields {sorted(unknown)}"
            missing = {'id', 'title', 'teach', 'start'} - set(step)
            assert not missing, f"{path.name}: step {step.get('id')!r} lacks {sorted(missing)}"
            step['start'] = merge(previous, step['start'])
            if 'solution' in step: previous = step['solution'] = merge(step['start'], step['solution'])
        result.append(chapter)
    ids = [s['id'] for c in result for s in c['steps']]
    assert len(ids) == len(set(ids)), 'step ids must be unique across chapters'
    return result


FENCE = re.compile(r'^```(helix(?:-error)?)\n(.*?)^```', re.S | re.M)


def examples(step):
    """(kind, source) for each runnable fence in a step's prose: `helix` must run, `helix-error` must fail."""
    return [(kind, body) for kind, body in FENCE.findall(step['teach'])]


def step_named(step_id):
    for chapter in chapters():
        for step in chapter['steps']:
            if step['id'] == step_id: return step
    raise KeyError(step_id)


def program(step, source):
    """The source actually executed: the learner's entries, then the step's probe defining `result`."""
    return source.rstrip('\n') + '\n' + step['probe'] if step.get('probe') else source


def grade(step, source, run):
    """Run the learner's source and compare with the step's expectation, using Helix's `eq`.

    Returns {passed, output, message}; `output` is the program's value as JSON (or its error text).
    """
    code, out, err = run(['exec', '--json', '-'], program(step, source))
    if code != 0:
        return {'passed': False, 'output': (out + err).strip(), 'message': 'the program failed; read the error and adjust'}
    try:
        value = json.loads(out)
    except ValueError:
        return {'passed': False, 'output': out.strip(), 'message': 'the program produced no value'}
    if isinstance(value, dict) and set(value) == {'value', 'objects'}: value = value['value']  # functions present: inspection graph
    if step.get('probe'): value = value['result'] if isinstance(value, dict) and 'result' in value else None
    shown = json.dumps(value, indent=2, sort_keys=True)
    if 'expect' not in step:
        return {'passed': True, 'output': shown, 'message': 'ran cleanly'}
    comparison = '!call [eq, !literal %s, !literal %s]' % (json.dumps(value), json.dumps(step['expect']))
    code, out, err = run(['exec', '--json', '-'], comparison)
    assert code == 0, err or out
    same = json.loads(out) is True
    return {'passed': same, 'output': shown,
            'message': 'matches' if same else 'result differs from what the task asks for: ' + json.dumps(step['expect'], sort_keys=True)}
