"""Disk restoration in fresh CLI processes; independent expected values and damaged states."""
import copy
import json
import tempfile
from pathlib import Path

import yaml


def verify(h):
    def invoke(command, source, *args):
        result = h.helix(command, '-', *args, stdin=source)
        assert result.returncode in (0, 1), (command, result)
        assert not result.stderr, (command, result.stderr)
        return result, yaml.safe_load(result.stdout)

    program = {'$call': ['let', {'f': {'$call': ['fn', [['x', 'integer']],
        {'$call': ['add', {'$ref': 'x'}, 3]}, 'integer']}}, {'$call': ['f', 7]}]}
    text = json.dumps(program)
    _, expected = invoke('run', text)
    found_local = False
    snapshots = []
    for steps in range(0, 80, 2):
        result, saved = invoke('snapshot', text, '--steps', str(steps))
        snapshots.append(saved)
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
                    found_local = True
        if saved['objects'][saved['machine']]['value']['status'] == 'finished':
            break
    assert found_local, 'no editable live parameter boundary exercised'

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
        _, state = invoke('restore', saved.stdout)
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
    saved_error = (h.root / 'tests/fixtures/snapshots/v1-error.json').read_text()
    _, restored_error = invoke('restore', saved_error)
    expected_error = yaml.safe_load((h.root / 'tests/fixtures/snapshots/v1-error.expected.yaml').read_text())
    assert restored_error == expected_error, restored_error

    # Sample established language interactions against the ordinary run oracle.
    for name in ('types_function_signatures', 'class_constraints', 'std_convergence',
                 'std_typing', 'types_type_time_preflight_forms', 'types_annotations',
                 'dogfood_errors', 'types_refinement_failures'):
        path = h.root / 'tests/cases' / f'{name}.yaml'
        ordinary = h.helix('run', str(path))
        for count in (0, 37):
            saved = h.helix('snapshot', str(path), '--steps', str(count))
            assert saved.returncode in (0, 1) and not saved.stderr, saved
            restored = h.helix('restore', '-', stdin=saved.stdout)
            assert restored.returncode == ordinary.returncode and restored.stdout == ordinary.stdout, (name, count, restored.stderr, restored.stdout[:300])
