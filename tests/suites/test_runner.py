"""Concurrency, ordering and exception contracts reached through the harness suite."""
import threading
from dataclasses import replace

from tooling.test_runner import TestContext, collect_cases, discover_cases


def verify_selection(h, directory):
    import contextlib
    import io

    first, second = directory / "run", directory / "check"
    first.mkdir()
    second.mkdir()
    cases = [first / "alpha.yaml", first / "beta.yaml", second / "alpha.yaml"]
    for case in cases:
        case.write_text("1")
        case.with_suffix(".expected.yaml").write_text("unchanged")
    directories = {first: "run", second: "check"}
    assert discover_cases(directories, h.root) == cases
    assert discover_cases(directories, h.root, ["alpha", "a*"]) == [cases[0], cases[2]]
    assert discover_cases(directories, h.root, [str(cases[1].relative_to(h.root))]) == [cases[1]]
    for patterns in (["missing"], ["alpha", "missing"], ["*.expected.yaml"]):
        try:
            discover_cases(directories, h.root, patterns)
        except ValueError as error:
            assert "matched no" in str(error)
        else:
            raise AssertionError(f"unmatched filter accepted: {patterns}")
    verify_discovery_failures(h, directory, directories)

    invoked = []
    def observe(args, **kwargs):
        invoked.append(args)
        return h.process_runner(args, **kwargs)
    selected = replace(h, commands=directories, process_runner=observe)
    with contextlib.redirect_stdout(io.StringIO()) as stdout, contextlib.redirect_stderr(io.StringIO()) as stderr:
        assert selected.test(False, ["golden"], patterns=["missing"]) == 2
        assert invoked == [], "an unmatched filter executed a process"
        assert selected.test(True, ["golden"], patterns=["beta"]) == 0, (stdout.getvalue(), stderr.getvalue())
        assert cases[1].with_suffix(".expected.yaml").read_text() != "unchanged"
        assert all(case.with_suffix(".expected.yaml").read_text() == "unchanged"
                   for case in (cases[0], cases[2]))
        invoked.clear()
        assert selected.test(False, ["golden", "robustness"], patterns=["beta", "b*"]) == 0
        file_runs = [args[2] for args in invoked if args[1] == "run" and args[2] != "-"]
        assert file_runs == [str(cases[1].relative_to(h.root))] * 2


def verify_discovery_failures(h, directory, healthy):
    """A missing corpus is a selection failure, never a successful zero-case suite."""
    import contextlib
    import io
    import json
    from unittest.mock import patch
    from tests.suites import contracts
    from tooling import test_policy, test_runner

    missing = directory / 'missing fixtures'
    not_directory = directory / 'not a directory'
    not_directory.write_text('not a corpus')
    empty = directory / 'empty fixtures'
    empty.mkdir()
    expected_only = directory / 'expected only'
    expected_only.mkdir()
    (expected_only / 'orphan.expected.yaml').write_text('unchanged')
    directory_only = directory / 'directory entry'
    directory_only.mkdir()
    (directory_only / 'not-a-file.yaml').mkdir()
    invalid = [{}, {missing: 'run'}, {not_directory: 'run'}, {empty: 'run'},
               {expected_only: 'run'}, {directory_only: 'run'}, {**healthy, empty: 'run'}]
    for configured in invalid:
        for patterns in (None, ['alpha']):
            try:
                discover_cases(configured, h.root, patterns)
            except ValueError as error:
                assert str(error), configured
            else:
                raise AssertionError(f'invalid fixture corpus accepted: {configured}, {patterns}')

    def summarize(context, rows):
        return {'command': 'test', 'status': 'passed', 'exit_code': 0, 'failure_count': 0,
                'results': [{**row, 'status': 'passed'} for row in rows]}

    for suite in ('golden', 'robustness'):
        # Explicit empty selections must not silently become all cases or no work.
        for configured, cases in (({missing: 'run'}, None), ({**healthy, empty: 'run'}, None),
                                  ({expected_only: 'run'}, None), (healthy, []),
                                  ({**healthy, missing: 'run'}, discover_cases(healthy, h.root))):
            context = replace(h, commands=configured)
            stdout, stderr = io.StringIO(), io.StringIO()
            plan = [{'suite': 'standard_library', 'action': 'run'}, {'suite': suite, 'action': 'run'}]
            with patch.object(test_policy, 'plan', return_value=plan), \
                 patch.object(test_policy, 'report', side_effect=summarize) as report, \
                 patch.object(TestContext, 'check_standard_library', return_value=[]) as standard, \
                 patch.object(TestContext, 'golden_case', return_value=[]) as golden, \
                 patch.object(TestContext, 'robustness', return_value=[]) as robust, \
                 patch.object(contracts, 'run', return_value=[]) as dispatch, \
                 contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = context.regression(False, [suite], cases=cases, output_format='json')
            result = json.loads(stdout.getvalue())
            assert code == 1 and result['version'] == 1 and result['command'] == 'test', result
            assert result['status'] == 'failed' and result['exit_code'] == 1 and result['failure_count'] == 1, result
            assert len(result['results']) == 1 and result['results'][0]['suite'] == 'orchestration', result
            assert result['results'][0]['failures'] and not stderr.getvalue(), (result, stderr.getvalue())
            assert not any(task.called for task in (standard, golden, robust, dispatch, report)), result

    for suite in ('startup', 'probe'):
        context = replace(h, commands={})
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(test_runner, 'discover_cases', side_effect=AssertionError('nonfixture discovery')), \
             patch.object(test_policy, 'plan', return_value=[{'suite': suite, 'action': 'run'}]) as plan, \
             patch.object(test_policy, 'report', side_effect=summarize) as report, \
             patch.object(contracts, 'run', return_value=[]) as dispatch, \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = context.regression(False, [suite], output_format='json')
        result = json.loads(stdout.getvalue())
        assert code == 0 and result['status'] == 'passed' and dispatch.call_count == 1, result
        assert not stderr.getvalue(), stderr.getvalue()
        if suite == 'startup':
            assert not plan.called and not report.called


def verify_import_context(h, directory):
    """Robustness must exercise the linked program, not accidental import errors."""
    import json
    import subprocess
    import yaml

    fixture = directory / 'fixture with spaces'
    (fixture / 'nested').mkdir(parents=True)
    (fixture / 'nested' / 'leaf.yaml').write_text('41\n')
    (fixture / 'sibling.yaml').write_text(json.dumps({'$call': ['import', 'nested/leaf.yaml']}))
    case = fixture / 'main.yaml'
    source = {'$call': ['add', {'$call': ['import', 'sibling.yaml']}, 1]}
    original = json.dumps(source)
    source['$call'][-1] = 2
    mutated = json.dumps(source)
    case.write_text(original)

    class OneMutation(TestContext):
        @staticmethod
        def mutations(text):
            assert text == original, text
            yield mutated

    for command in ('run', 'check --evaluate'):
        observed = []
        inject_failure = False

        def runner(args, **kwargs):
            result = h.process_runner(args, **kwargs)
            state = yaml.safe_load(result.stdout)
            observed.append((result.returncode, state.get('status'), state.get('root')))
            if inject_failure and state.get('root') == 43:
                return subprocess.CompletedProcess(args, -11, result.stdout, 'injected after linked evaluation')
            return result

        context = OneMutation(root=h.root, binary=h.binary, timeout=h.timeout,
                              commands={fixture: command}, process_runner=runner)
        assert context.robustness_case(case) == []
        assert observed == [(0, 'finished', 42), (0, 'finished', 43)], (command, observed)
        assert case.read_text() == original
        inject_failure = True
        failures = context.robustness_case(case)
        assert len(failures) == 1 and 'exit code -11' in failures[0], failures
        report = failures[0]
        evidence = h.root / next(line.removeprefix('evidence: ') for line in report.splitlines()
                                 if line.startswith('evidence: '))
        assert (evidence / 'input.yaml').read_text() == mutated
        assert yaml.safe_load((evidence / 'actual.yaml').read_text())['root'] == 43
        recipe = next(line.removeprefix('reproduce: ') for line in report.splitlines()
                      if line.startswith('reproduce: '))
        # Execute the emitted public recipe verbatim, including its build step;
        # the injected process fault is absent, but imports must still produce43.
        replayed = subprocess.run(recipe, shell=True, cwd=h.root, capture_output=True,
                                  text=True, timeout=120)
        assert replayed.returncode == 0, (recipe, replayed)
        replay_state = yaml.safe_load(replayed.stdout)
        assert replay_state['status'] == 'finished' and replay_state['root'] == 43, (recipe, replay_state)
        assert (replay_state.get('mode') == 'check') == (command == 'check --evaluate'), replay_state
        assert case.read_text() == original


def verify_timeout_output(h, directory):
    """Timeouts retain captured evidence without accepting a finished-looking state."""
    import subprocess

    case = directory / 'timeout.yaml'
    case.write_text('42\n')
    examples = [
        (b'status: finished\nroot: 42\n', b'progress caf\xc3\xa9 \xe2',
         'status: finished\nroot: 42\n', 'progress café \ufffd'),
        ('partial state', 'progress already terminated\n', 'partial state', 'progress already terminated\n'),
        (None, None, '', ''),
    ]
    for captured_out, captured_err, expected_out, expected_err in examples:
        def timeout(args, **kwargs):
            assert kwargs['text'] is True
            raise subprocess.TimeoutExpired(args, kwargs['timeout'], output=captured_out, stderr=captured_err)

        context = replace(h, commands={case.parent: 'run'}, process_runner=timeout)
        result = context.helix('run', '-', stdin='42')
        assert result.returncode == 124 and result.stdout == expected_out, result
        prefix = expected_err + ('\n' if expected_err and not expected_err.endswith('\n') else '')
        assert result.stderr.startswith(prefix + 'timed out: '), result
        assert result.stderr.count('timed out: ') == 1, result
        assert context.malformed_state(result.stdout, result.returncode) is not None, result

        failures = context.robustness_case(case)
        assert len(failures) == 1 and 'exit code 124' in failures[0], failures
        assert 'timed out:' in failures[0], failures
        evidence = h.root / next(line.removeprefix('evidence: ') for line in failures[0].splitlines()
                                 if line.startswith('evidence: '))
        assert (evidence / 'actual.yaml').read_text() == expected_out
        retained_stderr = (evidence / 'stderr.txt').read_text()
        assert retained_stderr.startswith(prefix + 'timed out: '), retained_stderr
        assert (evidence / 'input.yaml').read_text() == '42\n'
        assert case.read_text() == '42\n'


def verify_contexts(h):
    """Two overlapping invocations retain independent executable, root and timeout settings."""
    import subprocess
    from concurrent.futures import ThreadPoolExecutor

    directories = {h.root / "tests" / "cases": "run"}
    isolated = replace(h, commands=directories)
    directories.clear()
    assert list(isolated.commands.values()) == ["run"], "context retained the caller's mutable mapping"
    try:
        isolated.commands[h.root] = "check"
    except TypeError:
        pass
    else:
        raise AssertionError("context command mapping is mutable")

    barrier = threading.Barrier(2, timeout=5)
    def observe(args, **kwargs):
        barrier.wait()
        return subprocess.CompletedProcess(args, 0, (kwargs["cwd"], kwargs["timeout"], kwargs["input"]), "")
    first = replace(h, binary=h.root / "first", root=h.root / "runtime", timeout=1.25, process_runner=observe)
    second = replace(h, binary=h.root / "second", timeout=2.5, process_runner=observe)
    original = (h.root, h.binary, h.timeout, h.process_runner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = [pool.submit(context.helix, "run", "-", stdin=str(i))
                   for i, context in enumerate((first, second))]
        results = [future.result() for future in pending]
    for i, (context, result) in enumerate(zip((first, second), results)):
        assert result.args == [str(context.binary), "run", "-"]
        assert result.stdout == (context.root, context.timeout, str(i))
    assert (h.root, h.binary, h.timeout, h.process_runner) == original


def verify_standard_library_boundary(h):
    """Discovery/show failures retain both streams and replay the actual std command."""
    import shlex
    import subprocess
    import tempfile
    from pathlib import Path

    assert h.check_standard_library() == []
    with tempfile.TemporaryDirectory(dir=h.root / 'runtime', prefix='standard-boundary-') as temporary:
        root = Path(temporary)
        (root / 'std').mkdir()
        sources = {'alpha': 'answer: 42\n', 'beta': 'answer: 43\n'}
        for name, source in sources.items():
            (root / 'std' / f'{name}.yaml').write_text(source)
        catalog = 'std:alpha\nstd:beta\n'
        unknown = 'unknown standard module `unknown`; available: alpha, beta\n'
        responses = {('std',): (0, catalog, ''),
                     **{('std', name): (0, source, '') for name, source in sources.items()},
                     ('std', 'unknown'): (2, '', unknown)}
        scenarios = [
            (('std',), (0, catalog, 'unexpected discovery stderr\n')),
            (('std', 'alpha'), (0, sources['alpha'], 'unexpected source stderr\n')),
            (('std', 'alpha'), (0, 'changed source\n', '')),
            (('std', 'unknown'), (2, 'bogus source on stdout\n', unknown)),
            (('std', 'unknown'), (2, '', '')),
            (('std', 'unknown'), (2, '', 'unrelated host diagnostic\n')),
            (('std', 'alpha'), (-11, 'partial source\n', 'crash detail\n')),
            (('std',), 'timeout'),
        ]
        for invocation, damaged in scenarios:
            expected_output = []
            def runner(args, **kwargs):
                selected = tuple(args[1:])
                response = damaged if selected == invocation else responses[selected]
                if response == 'timeout':
                    error = subprocess.TimeoutExpired(args, kwargs['timeout'], output=b'std:alpha\n',
                                                      stderr=b'partial progress\n')
                    expected_output.append((124, 'std:alpha\n', f'partial progress\ntimed out: {error}'))
                    raise error
                if selected == invocation:
                    expected_output.append(response)
                return subprocess.CompletedProcess(args, *response)

            context = replace(h, root=root, process_runner=runner)
            failures = context.check_standard_library()
            assert len(failures) == 1 and len(expected_output) == 1, (invocation, damaged, failures)
            diagnostic = failures[0]
            code, stdout, stderr = expected_output[0]
            assert f'exit: {code}' in diagnostic, diagnostic
            recipe = next(line.removeprefix('reproduce: ') for line in diagnostic.splitlines()
                          if line.startswith('reproduce: '))
            assert shlex.split(recipe) == ['./manage.py', 'inspect', *invocation], recipe
            evidence = root / next(line.removeprefix('evidence: ') for line in diagnostic.splitlines()
                                   if line.startswith('evidence: '))
            assert (evidence / 'actual.yaml').read_text() == stdout
            assert (evidence / 'stderr.txt').read_text() == stderr
            assert (evidence / 'expected.yaml').read_text() == responses[invocation][1]
            assert all((root / 'std' / f'{name}.yaml').read_text() == source for name, source in sources.items())

        # Execute the final emitted catalog recipe without the injected timeout.
        # The current checkout, rather than the synthetic corpus, supplies modules.
        replayed = h.process_runner(shlex.split(recipe), cwd=h.root, capture_output=True,
                                    text=True, timeout=120)
        direct = h.helix('std')
        assert (replayed.returncode, replayed.stdout, replayed.stderr) == (
            direct.returncode, direct.stdout, direct.stderr), replayed

        from unittest.mock import Mock
        for name in ('missing', 'empty', 'not_directory', 'directory_entries'):
            corpus_root = root / name
            corpus_root.mkdir()
            if name == 'not_directory':
                (corpus_root / 'std').write_text('not a directory')
            elif name != 'missing':
                (corpus_root / 'std').mkdir()
                if name == 'directory_entries':
                    (corpus_root / 'std' / 'not-a-file.yaml').mkdir()
            runner = Mock(side_effect=AssertionError('invalid std corpus executed interpreter'))
            failures = replace(h, root=corpus_root, process_runner=runner).check_standard_library()
            assert len(failures) == 1 and not runner.called, (name, failures)


def verify_report_reliability(h):
    """Broken suite contracts and policy launch failures cannot erase completed work."""
    import contextlib
    import io
    import json
    import sys
    import tempfile
    from pathlib import Path
    from unittest.mock import patch
    from tests.suites import contracts
    from tooling import test_policy

    with tempfile.TemporaryDirectory(dir=h.root / 'runtime', prefix='report-reliability-') as temporary:
        directory = Path(temporary)
        case = directory / 'selected.yaml'
        case.write_text('42\n')
        selected_path = case.relative_to(h.root).as_posix()
        scenarios = [('none', None), ('string', 'not a failure list'), ('empty_string', ''),
                     ('mapping', {'failure': 'lost'}), ('empty_mapping', {}),
                     ('nonstring', [42]), ('policy_launch', [])]
        for output_format in ('text', 'json'):
            for name, invalid in scenarios:
                executed = []
                def runner(args, **kwargs):
                    if name == 'policy_launch' and 'policy.report' in (kwargs.get('input') or ''):
                        raise OSError('injected policy executable unavailable')
                    return h.process_runner(args, **kwargs)

                context = replace(h, commands={directory: 'run'}, process_runner=runner)
                def golden(path, bless):
                    assert path == case and bless is False
                    executed.append('golden')
                    print('earlier fixture stdout')
                    print('earlier fixture warning', file=sys.stderr)
                    return []

                def suite(suite_name, context):
                    executed.append(suite_name)
                    print(f'{suite_name} captured stdout')
                    print(f'{suite_name} captured stderr', file=sys.stderr)
                    return invalid if suite_name == 'middle' else []

                plan = [{'suite': suite_name, 'action': 'run'} for suite_name in ('golden', 'middle', 'later')]
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(test_policy, 'plan', return_value=plan), \
                     patch.object(test_policy, 'report', wraps=test_policy.report) as aggregate, \
                     patch.object(TestContext, 'golden_case', side_effect=golden), \
                     patch.object(contracts, 'run', side_effect=suite), \
                     contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    code = context.regression(False, ['golden', 'middle', 'later'], cases=[case],
                                              jobs=1, output_format=output_format)
                assert code == 1 and executed == ['golden', 'middle', 'later'], (name, code, executed)
                rows = aggregate.call_args.args[1]
                assert rows[0]['case_paths'] == [selected_path] and rows[0]['cases'] == 1, rows
                assert rows[0]['stdout'] == 'earlier fixture stdout\n' and rows[0]['stderr'] == 'earlier fixture warning\n', rows
                assert rows[1]['stdout'] == 'middle captured stdout\n' and rows[1]['stderr'] == 'middle captured stderr\n', rows
                assert all(isinstance(row['failures'], list) and all(isinstance(item, str) for item in row['failures'])
                           for row in rows), rows
                if name != 'policy_launch':
                    assert len(rows[1]['failures']) == 1 and 'TypeError' in rows[1]['failures'][0], rows
                if output_format == 'json':
                    report = json.loads(stdout.getvalue())
                    assert report['version'] == 1 and report['status'] == 'failed' and report['exit_code'] == 1, report
                    assert report['failure_count'] == 1 and not stderr.getvalue(), (report, stderr.getvalue())
                    assert [{key: value for key, value in row.items() if key != 'status'}
                            for row in report['results'][:3]] == rows, report
                    if name == 'policy_launch':
                        assert len(report['results']) == 4 and report['results'][3]['suite'] == 'orchestration', report
                        diagnostic = report['results'][3]['failures'][0]
                        assert '127' in diagnostic and 'injected policy executable unavailable' in diagnostic, diagnostic
                        evidence = h.root / next(line.removeprefix('evidence: ') for line in diagnostic.splitlines()
                                                 if line.startswith('evidence: '))
                        assert (evidence / 'actual.yaml').read_text() == ''
                        assert 'injected policy executable unavailable' in (evidence / 'stderr.txt').read_text()
                    else:
                        assert len(report['results']) == 3 and report['results'][1]['status'] == 'failed', report
                else:
                    for token in ('golden: passed', 'later: passed', 'test: failed'):
                        assert stdout.getvalue().count(token) == 1, stdout.getvalue()
                    middle_status = 'passed' if name == 'policy_launch' else 'failed'
                    assert stdout.getvalue().count(f'middle: {middle_status}') == 1, stdout.getvalue()
                    assert stdout.getvalue().count('orchestration: failed') == int(name == 'policy_launch'), stdout.getvalue()
                    for token in ('earlier fixture warning', 'middle captured stderr', 'later captured stderr'):
                        assert stderr.getvalue().count(token) == 1, stderr.getvalue()


def verify_completed_output(h):
    """Completed rows are visible before later work; JSON remains one final report."""
    import contextlib
    import io
    import json
    import sys
    from unittest.mock import patch

    from tests.suites import contracts
    from tooling import test_policy, test_runner

    class FlushedOutput(io.StringIO):
        def __init__(self):
            super().__init__()
            self.flushed = ''

        def flush(self):
            self.flushed = self.getvalue()

    for output_format in ('text', 'json'):
        for report_failure in (False, True):
            stdout, stderr = FlushedOutput(), FlushedOutput()
            checkpoints = []
            observations = []
            plan = [{'suite': 'first', 'action': 'run'},
                    {'suite': 'omitted', 'action': 'skip', 'reason': 'explicit skip'},
                    {'suite': 'second', 'action': 'run'}]

            def checkpoint():
                checkpoints.append((stdout.getvalue(), stderr.getvalue(), stdout.flushed, stderr.flushed))

            def run(suite, context):
                if suite == 'first':
                    print('failed suite output')
                    print('first warning', file=sys.stderr)
                    return ['independent failure']
                assert suite == 'second', suite
                checkpoint()  # A deterministic boundary before the later suite does work.
                print('successful suite chatter')
                print('second warning', file=sys.stderr)
                return []

            def report(context, rows):
                checkpoint()  # Aggregation can also block after all suites completed.
                observations.extend(rows)
                if report_failure:
                    raise test_policy.PolicyError('aggregation unavailable')
                return {'command': 'test', 'status': 'failed', 'exit_code': 1, 'failure_count': 1,
                        'results': [{**row, 'status': status} for row, status in
                                    zip(rows, ('failed', 'skipped', 'passed'))]}

            with patch.object(test_policy, 'plan', return_value=plan), \
                 patch.object(test_policy, 'report', side_effect=report), \
                 patch.object(contracts, 'run', side_effect=run), \
                 contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = h.regression(False, ['first', 'second'], output_format=output_format)
            assert code == 1 and len(checkpoints) == 2, (code, checkpoints)
            if output_format == 'json':
                assert all(point == ('', '', '', '') for point in checkpoints), checkpoints
                result = json.loads(stdout.getvalue())
                assert stderr.getvalue() == '', stderr.getvalue()
                assert (result['version'], result['command'], result['status'], result['exit_code']) == (1, 'test', 'failed', 1), result
                assert len(result['results']) == 3 + int(report_failure), result
                assert result['failure_count'] == 1 + int(report_failure), result
                assert result['results'][:3] == [{**row, 'status': status} for row, status in
                                               zip(observations, ('failed', 'skipped', 'passed'))], result
                if report_failure:
                    assert result['results'][3]['failures'] == ['aggregation unavailable'], result
            else:
                first_out, first_err, flushed_out, flushed_err = checkpoints[0]
                assert 'first: failed\n' in first_out and 'omitted: skipped (explicit skip)\n' in first_out, checkpoints
                assert 'failed suite output\n' in first_out, checkpoints
                assert 'first warning\n' in first_err and 'FAIL independent failure\n' in first_err, checkpoints
                assert first_out == flushed_out and first_err == flushed_err, checkpoints
                assert 'second: passed\n' in checkpoints[1][2] and 'second warning\n' in checkpoints[1][3], checkpoints
                for token in ('first: failed\n', 'omitted: skipped (explicit skip)\n', 'second: passed\n',
                              'failed suite output\n', 'test: failed ('):
                    assert stdout.getvalue().count(token) == 1, stdout.getvalue()
                assert 'successful suite chatter' not in stdout.getvalue(), stdout.getvalue()
                for token in ('first warning\n', 'second warning\n', 'FAIL independent failure\n'):
                    assert stderr.getvalue().count(token) == 1, stderr.getvalue()
                assert stdout.getvalue().count('orchestration: failed\n') == int(report_failure), stdout.getvalue()
                assert stderr.getvalue().count('FAIL aggregation unavailable\n') == int(report_failure), stderr.getvalue()

    # Bootstrap has no policy aggregation, but still emits its completed row once.
    stdout, stderr = FlushedOutput(), FlushedOutput()
    with patch.object(test_policy, 'plan', side_effect=AssertionError('bootstrap called policy')), \
         patch.object(test_policy, 'report', side_effect=AssertionError('bootstrap called policy')), \
         patch.object(contracts, 'run', return_value=[]), \
         contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        assert h.regression(False, ['startup']) == 0
    assert stdout.getvalue().count('startup: passed\n') == 1, stdout.getvalue()
    assert 'startup: passed\n' in stdout.flushed and stderr.getvalue() == '', (stdout.flushed, stderr.getvalue())


def verify(h):
    verify_contexts(h)
    verify_standard_library_boundary(h)
    verify_report_reliability(h)
    verify_completed_output(h)
    from tests.suites.test_orchestration import verify as verify_orchestration
    verify_orchestration(h)
    from tests.suites.golden_policy import verify as verify_golden_policy
    verify_golden_policy(h)
    caller = threading.get_ident()
    seen = []

    def serial(case):
        seen.append(threading.get_ident())
        return [str(case)]

    assert collect_cases(serial, range(6), 1) == list(map(str, range(6)))
    assert seen == [caller] * 6

    # A barrier proves overlap without relying on machine-dependent speedups.
    barrier = threading.Barrier(2, timeout=5)
    workers = set()
    lock = threading.Lock()

    def parallel(case):
        with lock:
            workers.add(threading.get_ident())
        barrier.wait()
        return [str(case)] if case % 2 == 0 else []

    assert collect_cases(parallel, range(6), 2) == ["0", "2", "4"]
    assert len(workers) == 2 and caller not in workers
    assert collect_cases(serial, [], 2) == []
    for jobs in (1, 2):
        def broken(case):
            raise RuntimeError("worker failed")
        try:
            collect_cases(broken, [0], jobs)
        except RuntimeError as error:
            assert str(error) == "worker failed"
        else:
            raise AssertionError("worker exception was swallowed")
    try:
        collect_cases(serial, [], 0)
    except ValueError:
        pass
    else:
        raise AssertionError("zero workers accepted")

    from tooling.cli import parse
    assert parse(["test", "--jobs", "1"]).jobs == 1
    assert parse(["quality", "test", "--jobs", "3"]).jobs == 3
    assert parse(["test", "--suite", "golden", "--case", "a*", "--case", "b*"]).case == ["a*", "b*"]

    import contextlib
    import io
    for args in (["--case", "a*"], ["--suite", "cli", "--case", "a*"],
                 ["--suite", "golden", "--suite", "cli", "--case", "a*"]):
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                parse(["test", *args])
            except SystemExit as error:
                assert error.code == 2
            else:
                raise AssertionError("case filter accepted for unrelated suites")
    for value in ("0", "-1"):
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                parse(["test", "--jobs", value])
            except SystemExit as error:
                assert error.code == 2
            else:
                raise AssertionError("nonpositive worker count accepted")

    import subprocess
    import tempfile
    from pathlib import Path

    class FailedOriginal(TestContext):
        @staticmethod
        def mutations(source):
            raise AssertionError("mutations consumed after the original already failed")
            yield source

    with tempfile.TemporaryDirectory(dir=h.root / "runtime", prefix="runner-") as directory:
        verify_timeout_output(h, Path(directory))
        verify_selection(h, Path(directory))
        verify_import_context(h, Path(directory))
        case = Path(directory) / "probe.yaml"
        case.write_text("1")
        failed = FailedOriginal(root=h.root, binary=h.binary, timeout=h.timeout,
                                commands={case.parent: "run"},
                                process_runner=lambda *args, **kwargs: subprocess.CompletedProcess([], 124, "", "timeout"))
        failures = failed.robustness_case(case)
        assert len(failures) == 1 and "exit code 124" in failures[0], failures
