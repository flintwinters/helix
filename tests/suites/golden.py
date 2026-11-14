"""Golden observations use general Helix assertions; invalid processes never bless."""
from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

FINISHED = 'status: finished\nroot: 42\n'


def verify(h):
    verify_comparisons(h)
    with tempfile.TemporaryDirectory(dir=h.root / 'runtime', prefix='golden-') as temporary:
        case = Path(temporary) / 'probe.yaml'
        case.write_text('42\n')
        expected_path = case.with_suffix('.expected.yaml')

        def exercise(*, output=FINISHED, code=0, expected=FINISHED, repeated=None,
                     replay=None, bless=False, ci=False, assertions=None):
            if expected is None:
                expected_path.unlink(missing_ok=True)
            else:
                expected_path.write_bytes(expected.encode('utf-8'))
            calls = []
            initial = (code, output, '')

            def runner(args, **kwargs):
                source = kwargs.get('input')
                if source is None:
                    response = initial if not calls or repeated is None else repeated
                    calls.append('fixture')
                elif not source.startswith('$literal:\n'):
                    return (h.process_runner(args, **kwargs) if assertions is None else
                            subprocess.CompletedProcess(args, *assertions))
                else:
                    assert source.startswith('$literal:\n'), source
                    calls.append('replay')
                    response = initial if replay is None else replay
                if response == 'timeout':
                    raise subprocess.TimeoutExpired(args, kwargs['timeout'], output=b'partial')
                if response == 'launch':
                    raise OSError('injected launch failure')
                return subprocess.CompletedProcess(args, *response)

            context = replace(h, commands={case.parent: 'run'}, process_runner=runner)
            with patch.dict('os.environ', {'CI': 'true' if ci else ''}):
                failures = context.golden_case(case, bless)
            if failures:
                assert (not expected_path.exists()) if expected is None else expected_path.read_bytes().decode('utf-8') == expected
            assert case.read_text() == '42\n'
            return failures, calls

        def stage(failures, expected):
            assert len(failures) == 1 and f'[{expected}]' in failures[0], failures

        failures, calls = exercise()
        assert failures == [] and calls == ['fixture', 'fixture', 'replay'], (failures, calls)
        # Identical YAML values with different spelling still fail exact goldens.
        for expected in (FINISHED.rstrip('\n'), 'status: finished\nroot: 0x2a\n', FINISHED.replace('\n', '\r\n'), '', None):
            failures, _ = exercise(expected=expected)
            stage(failures, 'golden')
            assert '--- expected' in failures[0] and '+++ actual' in failures[0], failures
            failures, _ = exercise(expected=expected, bless=True)
            assert failures == [] and expected_path.read_text() == FINISHED
        for repeated in ((0, FINISHED.replace('42', '43'), ''), (0, FINISHED, 'different stderr'),
                         (1, 'status: error\nerror: {kind: type, message: bad, line: 1, column: 1}\n', '')):
            for bless in (False, True):
                failures, calls = exercise(expected='wrong', repeated=repeated, bless=bless)
                stage(failures, 'determinism')
                assert calls == ['fixture', 'fixture', 'replay']
        faults = [(0, '[', ''), (0, 'status: finished\n', ''), (-11, '', ''),
                  (1, FINISHED, ''), (124, '', 'timeout'), 'timeout', 'launch']
        for fault in faults:
            for bless in (False, True):
                failures, _ = exercise(repeated=fault, expected=None, bless=bless)
                stage(failures, 'determinism')
                for expected in ('wrong', None):
                    failures, _ = exercise(replay=fault, expected=expected, bless=bless)
                    stage(failures, 'roundtrip')
        for bless in (False, True):
            failures, _ = exercise(expected='wrong', bless=bless,
                                   replay=(0, FINISHED.replace('42', '43'), ''))
            stage(failures, 'roundtrip')
            for code, output in ((-11, ''), (0, '['), (0, 'status: finished\n'), (1, FINISHED)):
                failures, calls = exercise(code=code, output=output, expected=None, bless=bless)
                stage(failures, 'state')
                assert calls == ['fixture']
        for code, output in (
            (1, 'status: error\nerror: {kind: type, message: bad, line: 1, column: 1}\n'),
            (1, 'status: signaled\npending_effect: {id: 0, operation: read, payload: null}\n'),
            (0, 'status: finished\nroot: {ref: function1}\nobjects: {}\n'),
        ):
            failures, calls = exercise(output=output, code=code, expected=output)
            assert failures == [] and calls == ['fixture', 'fixture'], (failures, calls)
        for response in ((-11, '', ''), (0, FINISHED, ''), (124, '', 'timeout')):
            for expected in ('wrong', None):
                failures, _ = exercise(bless=True, expected=expected, assertions=response)
                stage(failures, 'assertions')
        failures, calls = exercise(bless=True, expected='wrong', ci=True)
        assert len(failures) == 1 and 'disabled in CI' in failures[0] and calls == [], (failures, calls)
        failures, _ = exercise(ci=True)
        assert failures == [], failures


def verify_comparisons(h):
    """The same comparison composition handles arbitrary data, not golden phases."""
    from tooling.test_checks import compare
    observations = [
        {'name': 'config', 'actual': {'port': 80, 'enabled': True},
         'expected': {'enabled': True, 'port': 80}},
        {'name': 'ordered events', 'actual': ['start', 'stop'], 'expected': ['stop', 'start']},
        {'name': 'literal data', 'actual': {'$call': ['raise', 'hostile']},
         'expected': {'$call': ['raise', 'hostile']}},
    ]
    failed = compare(h, observations)
    assert [row['name'] for row in failed] == ['ordered events'], failed
    assert failed[0]['error']['kind'] == 'assertion', failed
    for response in ((-11, '', ''), (0, '[', ''), (0, FINISHED, ''),
                     (0, 'status: finished\nroot: {results: []}\n', ''), (124, '', 'timeout')):
        broken = replace(h, process_runner=lambda args, **kwargs: subprocess.CompletedProcess(args, *response))
        try:
            compare(broken, observations)
        except RuntimeError:
            pass
        else:
            raise AssertionError(f'accepted invalid comparison process: {response}')
