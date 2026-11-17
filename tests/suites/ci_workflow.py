"""Exercise the actual CI pipelines with independently failing producers and logs."""
import subprocess
import tempfile
from pathlib import Path

import yaml


def verify(h):
    workflow = yaml.safe_load((h.root / '.github/workflows/test.yml').read_text())
    job = workflow['jobs']['test']
    steps = [step for step in job['steps']
             if step.get('run', '').startswith('./manage.py test')]
    assert len(steps) == 2, 'both fast and full CI must exercise failure propagation'
    for step in steps:
        job_run = (job.get('defaults') or {}).get('run') or {}
        workflow_run = (workflow.get('defaults') or {}).get('run') or {}
        shell = step.get('shell', job_run.get('shell', workflow_run.get('shell')))
        assert shell == 'bash', f"{step['name']}: explicit Bash is required for pipefail"
        # Match GitHub's documented explicit-Bash invocation, using the actual
        # workflow pipeline unchanged. The stub stands in for a failing suite.
        with tempfile.TemporaryDirectory(dir=h.root / 'runtime', prefix='ci-') as temporary:
            root = Path(temporary)
            (root / 'runtime').mkdir()
            executable = root / 'manage.py'
            for code in (0, 7):
                executable.write_text(f'#!/bin/sh\nprintf "suite output\\n"\nprintf "suite error\\n" >&2\nexit {code}\n')
                executable.chmod(0o700)
                result = subprocess.run(['bash', '--noprofile', '--norc', '-eo', 'pipefail',
                                         '-c', step['run']], cwd=root, capture_output=True,
                                        text=True, timeout=5)
                assert result.returncode == code, (step['name'], result)
                assert (root / 'runtime/test.log').read_text() == 'suite output\nsuite error\n'
                assert result.stdout == 'suite output\nsuite error\n', result
            # A successful test command must also fail if tee cannot retain evidence.
            executable.write_text('#!/bin/sh\nprintf "suite output\\n"\n')
            (root / 'runtime/test.log').unlink()
            (root / 'runtime/test.log').mkdir()
            result = subprocess.run(['bash', '--noprofile', '--norc', '-eo', 'pipefail',
                                     '-c', step['run']], cwd=root, capture_output=True,
                                    text=True, timeout=5)
            assert result.returncode != 0 and result.stderr, (step['name'], result)
