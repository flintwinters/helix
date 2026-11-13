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
        verify_selection(h, Path(directory))
        verify_import_context(h, Path(directory))
        case = Path(directory) / "probe.yaml"
        case.write_text("1")
        failed = FailedOriginal(root=h.root, binary=h.binary, timeout=h.timeout,
                                commands={case.parent: "run"},
                                process_runner=lambda *args, **kwargs: subprocess.CompletedProcess([], 124, "", "timeout"))
        failures = failed.robustness_case(case)
        assert len(failures) == 1 and "exit code 124" in failures[0], failures
