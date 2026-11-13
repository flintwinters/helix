"""Tooling bridge contracts, called by manage.py test --suite cli."""
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml

from tooling.inventory import inventory, report


def verify(h):
    records = [{"path": 'src/a "quoted".rs', "directory": "src"},
               {"path": "std/雪.yaml", "directory": "std"},
               {"path": "src/second.rs", "directory": "src"}]
    source, result = report(h.helix, records)
    assert h.malformed_state(result.stdout, result.returncode) is None
    expected = [{"directory": "src", "count": 2, "files": [records[0]["path"], records[2]["path"]]},
                {"directory": "std", "count": 1, "files": [records[1]["path"]]}]
    assert yaml.safe_load(result.stdout)["root"]["report"] == expected
    assert h.helix("run", "-", stdin=source).stdout == result.stdout
    with tempfile.TemporaryDirectory(dir=h.root / "runtime", prefix="inventory-") as directory:
        replay = Path(directory) / "inventory.input.yaml"
        replay.write_text(source)
        assert h.helix("run", str(replay)).stdout == result.stdout
    assert yaml.safe_load(report(h.helix, [])[1].stdout)["root"]["report"] == []
    verify_command_boundary(h, records, source, expected)


def verify_command_boundary(h, records, source, expected):
    """Host observations cannot be hidden by malformed or falsely successful policy output."""
    import contextlib
    import io

    with tempfile.TemporaryDirectory(dir=h.root / 'runtime', prefix='inventory-boundary-') as temporary:
        root = Path(temporary)
        for record in records:
            path = root / record['path']
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('observed file')
        git = subprocess.CompletedProcess([], 0, '\0'.join(record['path'] for record in records) + '\0', '')

        def state(groups, **changes):
            return yaml.safe_dump({'status': 'finished', 'root': {'report': groups}, **changes})

        error = {'kind': 'type', 'message': 'injected failure', 'line': 1, 'column': 1}
        invalid = [
            (0, state(expected, status='error', error=error), ''),
            (0, state(expected, status='signaled', pending_effect={'id': 0, 'operation': 'time', 'payload': None}), ''),
            (0, '[', ''), (0, 'status: finished\n', ''),
            (0, 'status: finished\nroot: {}\n', ''),
            (0, state({}), ''), (0, state(expected), 'unexpected shim stderr\n'),
            (1, yaml.safe_dump({'status': 'error', 'error': error}), 'independent host failure detail\n'),
        ]
        malformed_groups = [
            [],
            [{**expected[0], 'count': 1}, expected[1]],
            [expected[0], {**expected[1], 'count': True}],
            [expected[0], {**expected[1], 'count': 1.0}],
            [{**expected[0], 'files': [records[0]['path'], records[0]['path']]}, expected[1]],
            [{**expected[0], 'files': [records[0]['path'], 'src/unobserved.rs']}, expected[1]],
            [{**expected[0], 'directory': 'std'}, {**expected[1], 'directory': 'src'}],
            [expected[0], {**expected[1], 'directory': 'src'}],
            [{**expected[0], 'extra': 'unsupported'}, expected[1]],
            [expected[0], {**expected[1], 'files': [7]}],
        ]
        invalid.extend((0, state(groups), '') for groups in malformed_groups)
        for code, output, errors in invalid:
            result = subprocess.CompletedProcess([], code, output, errors)
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch('tooling.inventory.subprocess.run', return_value=git), \
                 patch('tooling.inventory.report', return_value=(source, result)), \
                 contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = inventory(h.helix, root)
            assert status == 1 and not stdout.getvalue(), (result, status, stdout.getvalue())
            assert 'reproduce:' in stderr.getvalue() and 'evidence:' in stderr.getvalue(), stderr.getvalue()
            assert (root / 'runtime/inventory.input.yaml').read_text() == source
            assert (root / 'runtime/inventory.state.yaml').read_text() == output
            assert (root / 'runtime/inventory.stderr.txt').read_text() == errors
            evidence = root / next(line.removeprefix('evidence: ') for line in stderr.getvalue().splitlines()
                                   if line.startswith('evidence: '))
            assert (evidence / 'actual.yaml').read_text() == output
            assert (evidence / 'stderr.txt').read_text() == errors
            assert (evidence / 'input.yaml').read_text() == source

        # Use the real Helix shim for both nonempty and genuinely empty observations.
        for observation, groups in ((git, expected), (subprocess.CompletedProcess([], 0, '', ''), [])):
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch('tooling.inventory.subprocess.run', return_value=observation), \
                 contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = inventory(h.helix, root)
            assert status == 0 and not stderr.getvalue(), (status, stderr.getvalue())
            assert stdout.getvalue() == yaml.safe_dump(groups, allow_unicode=True, sort_keys=True), stdout.getvalue()
