"""Concurrency, ordering and exception contracts reached through the harness suite."""
import threading

from tooling.test_runner import collect_cases


def verify(h):
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

    import contextlib
    import io
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
    from unittest.mock import patch

    def unconsumed(source):
        raise AssertionError("mutations consumed after the original already failed")
        yield source

    with tempfile.TemporaryDirectory(dir=h.ROOT / "runtime", prefix="runner-") as directory:
        case = Path(directory) / "probe.yaml"
        case.write_text("1")
        with patch.object(h, "COMMANDS", {case.parent: "run"}), \
             patch.object(h, "mutations", unconsumed), \
             patch.object(h, "helix", return_value=subprocess.CompletedProcess([], 124, "", "timeout")), \
             patch.object(h, "test_failure", return_value="captured failure"):
            assert h.robustness_case(case) == ["captured failure"]
