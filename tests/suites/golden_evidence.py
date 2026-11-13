"""Compact golden decisions retain their complete host evidence on failure."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import tempfile

import yaml


def verify(h):
    with tempfile.TemporaryDirectory(dir=h.root / 'runtime', prefix='golden-evidence-') as temporary:
        case = Path(temporary) / 'probe.yaml'
        case.write_text('42\n')
        expected_path = case.with_suffix('.expected.yaml')
        output = yaml.safe_dump({'status': 'finished', 'root': {
            'text': 'raw observation 雪🙂\n' * 1000}}, allow_unicode=True, sort_keys=False)
        run_stderr = 'original warning 雪\nsecond line\n'
        replay_output = yaml.safe_dump({'status': 'finished', 'root': 'replayed 雪\n'},
                                      allow_unicode=True, sort_keys=False)
        replay_stderr = 'replay warning 🙂\nsecond line\n'
        policy_output = 'status: finished\nroot: {damaged: true}\n'
        policy_stderr = 'policy diagnostic 雪\n'
        retained = []
        for late in (False, True):
            for missing in (False, True):
                expected = None if missing else output
                if missing:
                    expected_path.unlink(missing_ok=True)
                else:
                    expected_path.write_text(expected)
                programs, executions = [], []

                def runner(args, **kwargs):
                    source = kwargs.get('input')
                    if source is None:
                        executions.append('fixture')
                        return subprocess.CompletedProcess(args, 0, output, run_stderr)
                    if source.startswith('$literal:\n'):
                        executions.append('replay')
                        return subprocess.CompletedProcess(args, 0, replay_output, replay_stderr)
                    programs.append(source)
                    if late and len(programs) == 1:
                        return h.process_runner(args, **kwargs)
                    return subprocess.CompletedProcess(args, 0, policy_output, policy_stderr)

                context = replace(h, commands={case.parent: 'run'}, process_runner=runner)
                failures = context.golden_case(case, True)
                assert len(failures) == 1 and '[policy]' in failures[0], failures
                assert executions == ['fixture', 'fixture'] + (['replay'] if late else []), executions
                assert len(programs) == (2 if late else 1), programs
                assert all(len(program.encode()) < 4096 for program in programs), programs
                assert all('raw observation' not in program and 'original warning' not in program
                           and 'replay warning' not in program for program in programs), programs
                evidence = h.root / next(line.removeprefix('evidence: ')
                    for line in failures[0].splitlines() if line.startswith('evidence: '))
                observation = {
                    'run': {'returncode': 0, 'stdout': output, 'stderr': run_stderr},
                    'repeated': {'returncode': 0, 'stdout': output, 'stderr': run_stderr},
                    'expected': expected, 'bless': True, 'root': h.root_section(output),
                    'replay': {'returncode': 0, 'root': h.root_section(replay_output),
                               'stdout': replay_output, 'stderr': replay_stderr} if late else None,
                }
                assert json.loads((evidence / 'observation.json').read_text()) == observation
                assert (evidence / 'input.yaml').read_text() == programs[-1]
                assert (evidence / 'actual.yaml').read_text() == policy_output
                assert (evidence / 'stderr.txt').read_text() == policy_stderr
                assert evidence not in [path for path, _ in retained], evidence
                retained.append((evidence, observation))
                assert case.read_text() == '42\n'
                assert (not expected_path.exists()) if missing else expected_path.read_text() == expected
        for evidence, observation in retained:
            assert json.loads((evidence / 'observation.json').read_text()) == observation
