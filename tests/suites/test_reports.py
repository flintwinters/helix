"""Failure evidence contracts, invoked by the harness suite."""
import shlex
import subprocess
import tempfile
from pathlib import Path

from tooling.test_reports import failure


def report_fixture(*rows):
    """Independent v1 report fixture shared by persisted-report boundary contracts."""
    count = sum(len(row["failures"]) for row in rows)
    return {"version": 1, "command": "test", "status": "failed" if count else "passed",
            "exit_code": int(bool(count)), "failure_count": count, "results": list(rows)}


def suite_fixture(name, status="failed"):
    row = {"suite": name, "status": status,
           "failures": [f"{name}: injected failure"] if status == "failed" else [],
           "stdout": "retained evidence\n", "stderr": ""}
    if status == "skipped":
        row["reason"] = "requires explicit selection"
    return row


def verify(h):
    verify_structured_results(h)
    verify_rerun_reports(h)
    with tempfile.TemporaryDirectory(dir=h.root / "runtime", prefix="reports-") as temporary:
        root = Path(temporary)
        case = root / "tests" / "check" / "with spaces.yaml"
        case.parent.mkdir(parents=True)
        case.write_text("original")
        result = subprocess.CompletedProcess([], 124, "actual", "timeout detail")
        report = failure(root, case, "robustness", "timeout", "check", result,
                         source="damaged", expected="expected\n")
        assert "[robustness] tests/check/with spaces.yaml" in report
        assert "exit: 124" in report and "timeout detail" in report
        assert "-expected" in report and "+actual" in report
        assert "No newline at end of file" in report
        replay_line = next(line for line in report.splitlines() if line.startswith("reproduce:"))
        args = shlex.split(replay_line.removeprefix("reproduce: "))
        assert args[:2] == ["./manage.py", "check"]
        assert (root / args[2]).read_text() == "damaged"
        evidence = root / next(line.removeprefix("evidence: ") for line in report.splitlines()
                               if line.startswith("evidence:"))
        assert (evidence / "actual.yaml").read_text() == "actual"
        assert (evidence / "expected.yaml").read_text() == "expected\n"
        assert (evidence / "stderr.txt").read_text() == "timeout detail"
        report = failure(root, case, "golden", "differs", "check", result,
                         expected="\n".join(str(i) for i in range(300)))
        assert "diff truncated" in report
        assert len(report.splitlines()) < 150
        assert case.read_text() == "original"
        verify_retained_evidence(root, case)
        verify_fuzz_evidence(h, root)


def evidence_path(root, report):
    return root / next(line.removeprefix('evidence: ') for line in report.splitlines()
                       if line.startswith('evidence: '))


def verify_retained_evidence(root, case):
    """Old reports keep coherent evidence through repeats and overlapping failures."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    def write(label, *, optional=True):
        result = subprocess.CompletedProcess([], -11, f'actual {label}', f'stderr {label}')
        report = failure(root, case, 'retention', label, 'run', result,
                         **({'source': f'source {label}', 'expected': f'expected {label}'} if optional else {}))
        return evidence_path(root, report)

    def snapshot(path):
        return {entry.name: entry.read_bytes() for entry in path.iterdir() if entry.is_file()}

    first = write('first')
    original = snapshot(first)
    second = write('second', optional=False)
    assert first != second and snapshot(first) == original, (first, second)
    assert snapshot(second) == {'actual.yaml': b'actual second', 'stderr.txt': b'stderr second'}, snapshot(second)
    third = write('third')
    assert len({first, second, third}) == 3 and snapshot(first) == original
    assert (third / 'input.yaml').read_text() == 'source third'
    assert (third / 'expected.yaml').read_text() == 'expected third'

    barrier = threading.Barrier(2, timeout=5)
    def concurrent(label):
        barrier.wait()
        return write(label)

    with ThreadPoolExecutor(max_workers=2) as workers:
        paths = list(workers.map(concurrent, ('left', 'right')))
    assert len({first, second, third, *paths}) == 5, paths
    for label, path in zip(('left', 'right'), paths):
        assert snapshot(path) == {name: f'{content} {label}'.encode() for name, content in (
            ('actual.yaml', 'actual'), ('stderr.txt', 'stderr'), ('input.yaml', 'source'), ('expected.yaml', 'expected'))}, path
    assert snapshot(first) == original


def verify_fuzz_evidence(h, root):
    """Minimized evidence belongs to the accepted input, with the actual check mode."""
    from dataclasses import replace
    from unittest.mock import patch
    from tests.suites import contracts

    (root / 'runtime').mkdir(exist_ok=True)
    for command in ('run', 'check'):
        attempted = []
        def runner(args, **kwargs):
            source = kwargs['input']
            attempted.append(source)
            assert args[1:] == [command, *(['--evaluate'] if command == 'check' else []), '-'], args
            if '!' in source:
                return subprocess.CompletedProcess(args, -11, f'crashed source={source!r}', f'crash stderr={source!r}')
            return subprocess.CompletedProcess(args, 0, 'status: finished\nroot: null\n', 'rejected candidate stderr')

        context = replace(h, root=root, process_runner=runner)
        with patch.object(contracts.random, 'Random') as random:
            random.return_value.choice.side_effect = lambda choices: command if choices == ('run', 'check') else '!' if isinstance(choices, str) else choices[0]
            random.return_value.randint.return_value = 1
            random.return_value.randrange.return_value = 0
            try:
                contracts.fuzz(context)
            except AssertionError as error:
                report = str(error)
            else:
                raise AssertionError('fuzz accepted an injected interpreter crash')
        assert attempted[-1] == '' and '!' in attempted, attempted
        assert '[fuzz]' in report and 'seed=901723' in report and 'sample=0' in report, report
        evidence = evidence_path(root, report)
        assert (evidence / 'input.yaml').read_text() == '!'
        assert (evidence / 'actual.yaml').read_text() == "crashed source='!'"
        assert (evidence / 'stderr.txt').read_text() == "crash stderr='!'"
        recipe = next(line.removeprefix('reproduce: ') for line in report.splitlines()
                      if line.startswith('reproduce: '))
        tokens = shlex.split(recipe)
        index = tokens.index(command)
        assert tokens[index:index + (3 if command == 'check' else 2)] == (
            ['check', '--evaluate', '-'] if command == 'check' else ['run', '-']), recipe
        assert '(cd' in tokens and '<' in tokens, recipe


def verify_rerun_reports(h):
    import copy
    import json
    from tooling.test_policy import load_rerun_report

    catalog = ["first", "golden", "second", "robustness", "docker_nginx"]
    valid = report_fixture(suite_fixture("first"), suite_fixture("second", "passed"),
                           suite_fixture("docker_nginx", "skipped"))
    with tempfile.TemporaryDirectory(dir=h.root / "runtime", prefix="rerun-reports-") as temporary:
        path = Path(temporary) / "previous.json"
        path.write_text(json.dumps(valid))
        original = path.read_bytes()
        assert load_rerun_report(path, catalog) == valid
        assert path.read_bytes() == original
        for report in (report_fixture(), report_fixture(suite_fixture("first", "passed")),
                       report_fixture({**suite_fixture("golden"), "cases": 3, "jobs": 2, "blessed": True}),
                       report_fixture(suite_fixture("standard_library"), suite_fixture("golden", "passed"))):
            path.write_text(json.dumps(report))
            assert load_rerun_report(path, catalog) == report

        damaged = [None, [], {}, {**valid, "version": 2}, {**valid, "version": True},
                   {**valid, "command": "build"}, {**valid, "status": "passed"},
                   {**valid, "exit_code": 0}, {**valid, "exit_code": True},
                   {**valid, "failure_count": 0}, {**valid, "failure_count": True},
                   {**valid, "results": {}}, report_fixture(suite_fixture("obsolete")),
                   report_fixture(suite_fixture("first"), suite_fixture("first"))]
        for field, value in (("suite", []), ("status", "unknown"), ("status", "passed"),
                             ("failures", "crash"), ("failures", [3]), ("stdout", []),
                             ("jobs", True), ("cases", -1), ("blessed", "yes"),
                             ("reason", "pretend skipped")):
            report = copy.deepcopy(valid)
            report["results"][0][field] = value
            damaged.append(report)
        for report in damaged:
            path.write_text(json.dumps(report))
            try:
                load_rerun_report(path, catalog)
            except ValueError:
                pass
            else:
                raise AssertionError(f"invalid persisted report accepted: {report}")
        for report, diagnostic in ((report_fixture(suite_fixture("docker_nginx")), "--suite docker_nginx"),
                                   (report_fixture(suite_fixture("orchestration")), "--suite harness"),
                                   (report_fixture(suite_fixture("standard_library")), "standard_library"),
                                   (report_fixture(suite_fixture("standard_library"),
                                                   suite_fixture("robustness", "skipped")), "standard_library")):
            path.write_text(json.dumps(report))
            try:
                load_rerun_report(path, catalog)
            except ValueError as error:
                assert diagnostic in str(error), error
            else:
                raise AssertionError(f"non-rerunnable failure accepted: {report}")
        for source in ("{", json.dumps(valid).replace('"version": 1', '"version": 1, "version": 1', 1)):
            path.write_text(source)
            try:
                load_rerun_report(path, catalog)
            except ValueError:
                pass
            else:
                raise AssertionError("malformed or duplicate-key JSON accepted")


def verify_structured_results(h):
    import contextlib
    import io
    import json
    from unittest.mock import patch

    from tests.suites import contracts

    def failing(name, context):
        print("diagnostic with unicode: 雪")
        return ["intentional failure"]

    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.object(contracts, "run", side_effect=failing), \
         contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        assert h.regression(False, ["startup"], output_format="json") == 1
    report = json.loads(stdout.getvalue())
    assert not stderr.getvalue()
    assert report["version"] == 1 and report["exit_code"] == report["failure_count"] == 1
    assert report["status"] == "failed"
    result = report["results"][0]
    assert result["suite"] == "startup" and result["failures"] == ["intentional failure"]
    assert "雪" in result["stdout"]

    stdout = io.StringIO()
    with patch.object(contracts, "run", side_effect=RuntimeError("unexpected crash")), \
         contextlib.redirect_stdout(stdout):
        assert h.regression(False, ["startup"], output_format="json") == 1
    assert "unexpected crash" in json.loads(stdout.getvalue())["results"][0]["failures"][0]
