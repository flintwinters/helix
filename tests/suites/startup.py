"""Discovery import boundaries, exercised without compiling or running Helix."""
import subprocess
import sys


def verify(h):
    # Assert the import boundary rather than a machine-dependent timing limit.
    for args in [(), ("--help",), ("--help-json",), ("runtime", "--help"),
                 ("test", "--list"), ("quality", "test", "--describe")]:
        profiled = subprocess.run(
            [sys.executable, "-X", "importtime", str(h.root / "manage.py"), *args],
            cwd=h.root, capture_output=True, text=True, timeout=10,
        )
        assert profiled.returncode == 0, profiled.stderr
        imported = {line.rsplit("|", 1)[-1].strip() for line in profiled.stderr.splitlines()
                    if line.startswith("import time:")}
        assert not imported & {"subprocess", "tempfile", "difflib", "shlex", "yaml"}, imported

    # Default discovery is scoped; the complete machine catalog is opt-in.
    from tooling.cli import parse
    import contextlib
    import io
    import json
    from tooling.output import test_report

    def help_output(args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            assert parse(args) is None
        return output.getvalue()

    brief = help_output([])
    full = help_output(["--help-json"])
    assert "usage:" in brief and len(brief) < len(full) / 3
    assert "tree" in json.loads(full)
    assert "--suite" not in help_output(["runtime"])
    assert parse(["build"]).format == "text"
    assert parse(["--format=json", "build"]).format == "json"

    results = [
        {"suite": "golden", "status": "passed", "failures": [],
         "stdout": "successful chatter\n", "stderr": "warning\n", "cases": 7, "blessed": True},
        {"suite": "robustness", "status": "skipped", "failures": [], "reason": "blessing"},
        {"suite": "broken", "status": "failed", "failures": ["reproduce: command\nevidence: runtime/path"],
         "stdout": "failure context\n", "stderr": "failure stderr\n"},
    ]
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        assert test_report(results) == 1
    assert "successful chatter" not in stdout.getvalue()
    assert "7 cases; expectations rewritten" in stdout.getvalue()
    assert "robustness: skipped (blessing)" in stdout.getvalue()
    assert "3 suites, 1 skipped, 1 failures" in stdout.getvalue()
    assert "failure context" in stdout.getvalue()
    assert stderr.getvalue().count("reproduce: command") == 1
    assert "evidence: runtime/path" in stderr.getvalue()
    assert "warning" in stderr.getvalue() and "failure stderr" in stderr.getvalue()
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        assert test_report(results, output_format="json") == 1
    assert json.loads(stdout.getvalue())["results"] == results
