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


def verify(h):
    verify_contexts(h)
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
        case = Path(directory) / "probe.yaml"
        case.write_text("1")
        failed = FailedOriginal(root=h.root, binary=h.binary, timeout=h.timeout,
                                commands={case.parent: "run"},
                                process_runner=lambda *args, **kwargs: subprocess.CompletedProcess([], 124, "", "timeout"))
        failures = failed.robustness_case(case)
        assert len(failures) == 1 and "exit code 124" in failures[0], failures
