"""Failure evidence contracts, invoked by the harness suite."""
import shlex
import subprocess
import tempfile
from pathlib import Path

from tooling.test_reports import failure


def verify(h):
    verify_structured_results(h)
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
