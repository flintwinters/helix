"""Independent orchestration oracles and hostile interpreter observations."""
import contextlib
import io
import json
import subprocess
from dataclasses import replace
from unittest.mock import patch

import yaml

from tooling import test_policy, test_runner
from tests.suites import contracts


def verify(h):
    catalog = ["first", "golden", "second", "robustness"]
    for requested in (None, [], ["second"], ["second", "first", "second"],
                      ["golden"], ["robustness"], ["robustness", "golden"]):
        for bless in (False, True):
            selected = set(requested or catalog)
            names = [name for name in catalog if name in selected - {"golden", "robustness"}]
            if selected & {"golden", "robustness"}:
                names.append("standard_library")
            names.extend(name for name in ("golden", "robustness") if name in selected)
            actual = test_policy.plan(h, catalog, requested, bless)
            assert [row["suite"] for row in actual] == names, actual
            assert [row["suite"] for row in actual if row["action"] == "skip"] == (
                ["robustness"] if bless and "robustness" in selected else [])
    assert test_policy.plan(h, [], [], False) == []
    observations = [{"suite": "first", "failures": ["host crash"], "stdout": "evidence", "stderr": ""},
                    {"suite": "second", "failures": [], "stdout": "", "stderr": ""}]
    actual = test_policy.report(h, observations)
    assert actual["failure_count"] == actual["exit_code"] == 1
    assert [row["status"] for row in actual["results"]] == ["failed", "passed"]
    assert test_policy.report(h, [])["exit_code"] == 0
    # Actual suite-catalog scale with retained process output, not only tiny rows.
    large = [{"suite": name, "failures": [], "stdout": "retained log\n" * 256, "stderr": ""}
             for name in test_runner.SUITES]
    retained = test_policy.report(h, large)
    assert [{key: value for key, value in row.items() if key != "status"}
            for row in retained["results"]] == large

    def bad_context(response):
        def runner(args, **kwargs):
            if response == "timeout":
                raise subprocess.TimeoutExpired(args, kwargs["timeout"])
            return subprocess.CompletedProcess(args, *response)
        return replace(h, process_runner=runner)

    def state(root):
        return 0, yaml.safe_dump({"status": "finished", "root": root}), ""

    faults = [(-11, "", ""), (124, "", "timeout"), (0, "[", ""), "timeout", state(None)]
    for response in faults:
        for operation in (lambda context: test_policy.plan(context, catalog, None, False),
                          lambda context: test_policy.report(context, observations)):
            try:
                operation(bad_context(response))
            except test_policy.PolicyError:
                pass
            else:
                raise AssertionError(f"broken policy accepted: {response}")
    for response in ([], [{"suite": "first", "action": "skip", "reason": "hidden"}],
                     [{"suite": "first", "action": "run"}] * 2,
                     [{"suite": "unauthorized", "action": "run"}],
                     [{"suite": [], "action": "run"}]):
        try:
            test_policy.plan(bad_context(state(response)), ["first"], ["first"], False)
        except test_policy.PolicyError:
            pass
        else:
            raise AssertionError(f"unsafe plan accepted: {response}")
    for response, bless in (([{"suite": "golden", "action": "run"}], False),
                            ([{"suite": "golden", "action": "run"},
                              {"suite": "standard_library", "action": "run"}], False),
                            ([{"suite": "standard_library", "action": "run"},
                              {"suite": "robustness", "action": "run"}], True)):
        suite = "robustness" if bless else "golden"
        try:
            test_policy.plan(bad_context(state(response)), [suite], [suite], bless)
        except test_policy.PolicyError:
            pass
        else:
            raise AssertionError(f"prerequisite or blessing boundary bypassed: {response}")
    for damaged in ({**actual, "results": []}, {**actual, "exit_code": 0},
                    {**actual, "failure_count": 0}, {**actual, "status": "passed"},
                    {**actual, "results": [{**actual["results"][0], "failures": []}, actual["results"][1]]}):
        try:
            test_policy.report(bad_context(state(damaged)), observations)
        except test_policy.PolicyError:
            pass
        else:
            raise AssertionError(f"host failure hidden: {damaged}")

    # Production wiring must stop before running tasks on a broken plan and must
    # preserve all task failures when final report generation fails.
    for phase in ("plan", "report"):
        def runner(args, **kwargs):
            if f"policy.{phase}" in (kwargs.get("input") or ""):
                return subprocess.CompletedProcess(args, -11, "", "policy crash")
            return h.process_runner(args, **kwargs)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(test_runner, "SUITES", {"first": "probe", "second": "probe"}), \
             patch.object(contracts, "run", side_effect=[["host failure"], []]) as dispatch, \
             contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            assert replace(h, process_runner=runner).regression(False, ["first", "second"], output_format="json") == 1
        report = json.loads(stdout.getvalue())
        assert dispatch.call_count == (0 if phase == "plan" else 2)
        assert report["failure_count"] == (1 if phase == "plan" else 2)
        if phase == "report":
            assert report["results"][0]["failures"] == ["host failure"]
