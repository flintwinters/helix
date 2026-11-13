"""Disk restoration in fresh CLI processes; independent expected values and damaged states."""
import copy
import difflib
import json
import tempfile
from pathlib import Path

import yaml


def verify(h):
    def invoke(command, source, *args):
        result = h.helix(command, '-', *args, stdin=source)
        assert result.returncode in (0, 1), (command, result)
        assert not result.stderr, (command, result.stderr)
        # Checkpoints have a canonical JSON wire spelling. Avoid repeatedly
        # reparsing large embedded source graphs through the slower YAML parser.
        parse = json.loads if command == 'snapshot' or '--steps' in args else yaml.safe_load
        return result, parse(result.stdout)

    program = {'$call': ['let', {'f': {'$call': ['fn', [['x', 'integer']],
        {'$call': ['add', {'$ref': 'x'}, 3]}, 'integer']}}, {'$call': ['f', 7]}]}
    text = json.dumps(program)
    _, expected = invoke('run', text)
    found_local = False
    snapshots = []
    for steps in range(0, 80, 2):
        result, saved = invoke('snapshot', text, '--steps', str(steps))
        snapshots.append(saved)
        # A fresh process must preserve the canonical document when no transition
        # occurs: allocator identities must not leak into reviewable state diffs.
        canonical, unchanged = invoke('restore', result.stdout, '--steps', '0')
        assert unchanged == saved, ('zero-step snapshot changed', steps)
        assert canonical.stdout == result.stdout, ('snapshot spelling changed', steps)
        restored, state = invoke('restore', result.stdout)
        assert restored.returncode == 0 and state == expected, (steps, state)
        for row in saved['objects'].values():
            if row['kind'] != 'evaluator':
                continue
            e = row['value']
            # An active parameter frame supplies the next lexical read. Changing it
            # should change the result without re-running its caller or its source.
            if (e['environment'] and e['requests'] and e['requests'][-1]['kind'] == 'Eval'
                    and e['environment'][-1]['args'] == [{'integer': 7}]):
                changed = copy.deepcopy(saved)
                for candidate in changed['objects'].values():
                    if candidate['kind'] == 'evaluator' and candidate['value']['environment']:
                        candidate['value']['environment'][-1]['args'] = [{'integer': 11}]
                _, branch = invoke('restore', json.dumps(changed))
                if branch.get('root') == 14:
                    canonical_branch, _ = invoke('restore', json.dumps(changed), '--steps', '0')
                    delta = list(difflib.unified_diff(result.stdout.splitlines(),
                        canonical_branch.stdout.splitlines()))
                    removed = [line[1:] for line in delta if line.startswith('-') and not line.startswith('---')]
                    added = [line[1:] for line in delta if line.startswith('+') and not line.startswith('+++')]
                    assert removed and len(removed) == len(added), delta
                    assert all(old.replace('7', '11') == new for old, new in zip(removed, added)), delta
                    found_local = True
        if saved['objects'][saved['machine']]['value']['status'] == 'finished':
            break
    assert found_local, 'no editable live parameter boundary exercised'

    # A substantial nested-call workload exposes progress and locals directly in
    # the checkpoint. Independent arithmetic checks guard against two executions
    # agreeing on the same wrong answer.
    workload = {'$call': ['let', {
        'inner': {'$call': ['fn', ['x'], {'$call': ['mul', {'$ref': 'x'}, 2]}]},
        'outer': {'$call': ['fn', ['n'], {'$call': ['inner', {'$ref': 'n'}]}]},
    }, {'$call': ['map', {'$ref': 'outer'}, list(range(120))]}]}
    workload_text = json.dumps(workload)
    _, uninterrupted = invoke('run', workload_text)
    assert uninterrupted == {'status': 'finished', 'root': [2 * n for n in range(120)]}, uninterrupted
    nested_boundary = False
    observed = []
    for steps in range(400, 440):
        checkpoint, saved = invoke('snapshot', workload_text, '--steps', str(steps))
        evaluators = [row['value'] for row in saved['objects'].values() if row['kind'] == 'evaluator']
        iterations = [request['iteration'] for evaluator in evaluators
                      for request in evaluator['requests'] if request['kind'] == 'Iterated']
        observed.append((steps, [[r['kind'] for r in e['requests']] for e in evaluators]))
        if not iterations or not any(
                sum(request['kind'] == 'CallEnd' for request in e['requests']) >= 2
                and any(frame['args'] for frame in e['environment']) for e in evaluators):
            continue
        progress = iterations[0]
        assert 0 < len(progress['output']) < 120, progress
        assert progress['output'] == [{'integer': 2 * n} for n in range(len(progress['output']))], progress
        advanced, advanced_state = invoke('restore', checkpoint.stdout, '--steps', '1')
        programs = {name: row for name, row in saved['objects'].items() if row['kind'] == 'program'}
        assert all(advanced_state['objects'].get(name) == row for name, row in programs.items()), 'source-object churn'
        delta = list(difflib.unified_diff(checkpoint.stdout.splitlines(), advanced.stdout.splitlines()))
        churn = sum(line.startswith(('+', '-')) and not line.startswith(('+++', '---')) for line in delta)
        assert 0 < churn < 100, ('one inner-call transition diff', churn, delta)
        _, resumed = invoke('restore', checkpoint.stdout)
        assert resumed == uninterrupted, resumed
        nested_boundary = True
        break
    assert nested_boundary, ('no nested-call iteration checkpoint exercised', observed)

    # Linking happens at initial load. Restore must work after the original module
    # disappears; it must neither open imports nor re-run an import resolver.
    with tempfile.TemporaryDirectory(dir=h.root / 'runtime', prefix='snapshot-import-') as directory:
        base = Path(directory)
        module = base / 'helper.yaml'
        module.write_text('value: 42\n')
        source = base / 'main.yaml'
        source.write_text('helper: {$call: [import, "helper.yaml"]}\nanswer: {$ref: helper.value}\n')
        saved = h.helix('snapshot', str(source), '--steps', '3')
        assert saved.returncode == 0, saved
        module.unlink()
        source.unlink()
        # Transfer only the checkpoint to a distinct working directory. Embedded
        # imports must suffice even when neither source file still exists.
        destination = base / 'other-checkout'
        destination.mkdir()
        checkpoint = destination / 'machine.json'
        checkpoint.write_text(saved.stdout)
        moved = h.helix('restore', str(checkpoint), cwd=destination)
        assert moved.returncode == 0 and not moved.stderr, moved
        state = yaml.safe_load(moved.stdout)
        assert state['root']['answer'] == 42, state

    # Compatibility and strict schema rejection; no runtime panic or host effect.
    baseline = snapshots[0]
    mutations = []
    for key, value in [('version', 0), ('version', 999), ('runtime', 'unknown'), ('machine', 'missing')]:
        changed = copy.deepcopy(baseline); changed[key] = value; mutations.append(changed)
    for name, row in baseline['objects'].items():
        if row['kind'] == 'program':
            changed = copy.deepcopy(baseline)
            changed['objects'][name]['value']['nodes'][0]['location']['path'] = ['wrong']
            mutations.append(changed)
        if row['kind'] == 'evaluator':
            location = {'source': '<root>', 'path': []}
            atomic = {'kind': 'Atomic', 'atomic': {'site': location, 'nodes': [location],
                'operation': {'kind': 'Builtin', 'name': 'step', 'values': [{'null': True}]},
                'answers': [], 'cause': None, 'predicate_failed': False}}
            collect = {'kind': 'Collect', 'site': location, 'actor': {'kind': 'Builtin', 'name': 'add'},
                       'nodes': [location], 'keys': None, 'values': []}
            for requests in ([atomic], [collect], [{'kind': 'Unknown'}], [{'kind': 'Eval', 'node': {'source': '<root>', 'path': ['missing']}}]):
                changed = copy.deepcopy(baseline); changed['objects'][name]['value']['requests'] = requests
                mutations.append(changed)
    for changed in mutations:
        result, report = invoke('restore', json.dumps(changed))
        assert result.returncode == 1 and report['errors'][0]['kind'] == 'snapshot', report
    for damaged in ('', '{', json.dumps({'status': 'finished', 'root': 42})):
        result, report = invoke('restore', damaged)
        assert result.returncode == 1 and report['errors'][0]['kind'] == 'snapshot', report

    compatibility = (h.root / 'tests/fixtures/snapshots/v1-ready.json').read_text()
    _, restored = invoke('restore', compatibility)
    assert restored == {'status': 'finished', 'root': 42}, restored
    for spelling in (json.dumps(json.loads(compatibility), sort_keys=True), yaml.safe_dump(json.loads(compatibility))):
        _, reordered = invoke('restore', spelling)
        assert reordered == restored, reordered
    saved_error = (h.root / 'tests/fixtures/snapshots/v1-error.json').read_text()
    _, restored_error = invoke('restore', saved_error)
    expected_error = yaml.safe_load((h.root / 'tests/fixtures/snapshots/v1-error.expected.yaml').read_text())
    assert restored_error == expected_error, restored_error

    # Sample established language interactions against the ordinary run oracle.
    for name in ('types_function_signatures', 'class_constraints', 'std_convergence',
                 'std_typing', 'types_type_time_preflight_forms', 'types_annotations',
                 'dogfood_errors', 'types_refinement_failures', 'nginx_ssh_request'):
        path = h.root / 'tests/cases' / f'{name}.yaml'
        ordinary = h.helix('run', str(path))
        counts = (0, 37, 2_000_000) if name == 'nginx_ssh_request' else (0, 37)
        for count in counts:
            saved = h.helix('snapshot', str(path), '--steps', str(count))
            assert saved.returncode in (0, 1) and not saved.stderr, saved
            restored = h.helix('restore', '-', stdin=saved.stdout)
            assert restored.returncode == ordinary.returncode and restored.stdout == ordinary.stdout, (
                name, count, {'run_exit': ordinary.returncode, 'restore_exit': restored.returncode},
                restored.stderr, ''.join(difflib.unified_diff(ordinary.stdout.splitlines(True),
                    restored.stdout.splitlines(True), fromfile='uninterrupted', tofile='restored'))[:4000])

    from tests.suites.promised_peers import verify as verify_peers
    verify_peers(h)
