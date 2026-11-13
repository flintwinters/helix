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
from tests.suites.test_reports import report_fixture, suite_fixture


def verify(h):
    verify_policy_annotations(h)
    verify_rerun_wiring(h)
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
    docker_catalog = ["first", "docker_nginx"]
    default = test_policy.plan(h, docker_catalog, None, False)
    assert default[1]["action"] == "skip" and "--suite docker_nginx" in default[1]["reason"], default
    assert test_policy.plan(h, docker_catalog, ["docker_nginx"], False) == [
        {"suite": "docker_nginx", "action": "run"}]
    previous = report_fixture(suite_fixture("second"), suite_fixture("first"),
                              suite_fixture("golden", "passed"), suite_fixture("robustness", "skipped"))
    assert test_policy.rerun(h, catalog, previous) == ["first", "second"]
    # Rerun selection needs statuses only: large captured logs must not consume
    # interpreter source/work budgets or become policy input accidentally.
    chatter = "UNNEEDED_REPLAY_EVIDENCE" * 65536
    noisy = {**previous, "results": [{**row, "stdout": chatter} for row in previous["results"]]}
    def compact_runner(args, **kwargs):
        assert "UNNEEDED_REPLAY_EVIDENCE" not in (kwargs.get("input") or "")
        return h.process_runner(args, **kwargs)
    assert test_policy.rerun(replace(h, process_runner=compact_runner), catalog, noisy) == ["first", "second"]
    assert test_policy.rerun(h, catalog, report_fixture()) == []
    assert test_policy.rerun(h, catalog, report_fixture(suite_fixture("first", "passed"),
                                                      suite_fixture("robustness", "skipped"))) == []
    for companions, expected in (([suite_fixture("golden", "passed")], ["golden"]),
                                  ([suite_fixture("robustness", "passed")], ["robustness"]),
                                  ([suite_fixture("golden", "passed"), suite_fixture("robustness", "passed")],
                                   ["golden", "robustness"]),
                                  ([suite_fixture("golden", "passed"), suite_fixture("robustness", "skipped")], ["golden"])):
        prior = report_fixture(suite_fixture("standard_library"), *companions)
        selected = test_policy.rerun(h, catalog, prior)
        assert selected == expected, (prior, selected)
        plan = test_policy.plan(h, catalog, selected, False)
        assert [row["suite"] for row in plan] == ["standard_library", *expected], plan
    observations = [{"suite": "first", "failures": ["host crash"], "stdout": "evidence", "stderr": "",
                     "cases": 3, "jobs": 2, "blessed": True},
                    {"suite": "second", "failures": [], "stdout": "", "stderr": ""}]
    actual = test_policy.report(h, observations)
    assert actual == {"command": "test", "failure_count": 1, "exit_code": 1, "status": "failed",
                      "results": [{**row, "status": status} for row, status in
                                  zip(observations, ("failed", "passed"))]}, actual
    assert test_policy.report(h, [])["exit_code"] == 0
    # Actual suite-catalog scale with retained process output, not only tiny rows.
    large = [{"suite": name, "failures": [], "stdout": "retained log\n" * 256, "stderr": ""}
             for name in test_runner.SUITES]
    retained = test_policy.report(h, large)
    assert retained == {"command": "test", "failure_count": 0, "exit_code": 0, "status": "passed",
                        "results": [{**row, "status": "passed"} for row in large]}, retained
    # Counting failures must neither flatten away their owning suites nor discard
    # messages. Repeated diagnostics are separate failures and retain their order.
    failed_rows = [{**row, "failures": [
        f'{row["suite"]}: diagnostic {max(0, index - 1)}\n'
        + ('expected value, actual value, captured stderr, and replay source\n' * 16)
        for index in range(2 + suite_index % 3)]}
        for suite_index, row in enumerate(large)]
    retained = test_policy.report(h, failed_rows)
    assert retained == {"command": "test", "failure_count": sum(2 + index % 3 for index in range(len(large))),
                        "exit_code": 1, "status": "failed",
                        "results": [{**row, "status": "failed"} for row in failed_rows]}, retained

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
                          lambda context: test_policy.report(context, observations),
                          lambda context: test_policy.rerun(context, catalog, previous)):
            try:
                operation(bad_context(response))
            except test_policy.PolicyError:
                pass
            else:
                raise AssertionError(f"broken policy accepted: {response}")
    # Persisted failures are observations, not authority for a damaged policy to omit
    # failures, run passing/skipped suites, provision Docker, or execute shell text.
    for response in ([], ["first"], ["first", "second", "golden"],
                     ["first", "second", "robustness"], ["first", "second", "docker_nginx"],
                     ["first", "second", "$(touch forbidden)"], ["second", "first"],
                     ["first", "first", "second"], [{"suite": "first"}], None):
        try:
            test_policy.rerun(bad_context(state(response)), catalog, previous)
        except test_policy.PolicyError:
            pass
        else:
            raise AssertionError(f"unsafe rerun selection accepted: {response}")
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
    for requested, response in ((None, [{"suite": "docker_nginx", "action": "run"}]),
            (["docker_nginx"], [{"suite": "docker_nginx", "action": "skip", "reason": "hidden"}])):
        try:
            test_policy.plan(bad_context(state(response)), ["docker_nginx"], requested, False)
        except test_policy.PolicyError:
            pass
        else:
            raise AssertionError(f"Docker opt-in boundary bypassed: {response}")
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


def verify_policy_annotations(h):
    """Exercise real function contracts independently of the Python response validator."""
    import copy
    import tempfile
    from pathlib import Path

    original = h.root / "tooling" / "test_policy.yaml"

    def execute(operation, arguments, module=original):
        source = {"$call": ["let", {"policy": {"$call": ["import", str(module)]}},
                  {"probe": {"$call": [f"policy.{operation}", *({"$literal": value} for value in arguments)]}}]}
        process = h.helix("run", "-", stdin=yaml.safe_dump(source))
        assert h.malformed_state(process.stdout, process.returncode) is None, process
        return process.returncode, yaml.safe_load(process.stdout)

    # Each failure would otherwise enter policy helpers or silently manufacture a
    # malformed report; the annotation must name the rejected public parameter.
    invalid = [
        ("plan", [["first", 7], [], False], "catalog"),
        ("plan", [["first"], "first", False], "requested"),
        ("plan", [["first"], [], "false"], "bless"),
        ("rerun", [["first"], {"results": [{"suite": "first", "status": "unknown"}]}], "previous"),
        ("result", [{"suite": "first", "failures": "lost failure list"}], "observation"),
        ("result", [{"suite": "first", "failures": [], "stdout": 4}], "observation"),
        ("report", [[{"suite": "first", "failures": [None]}]], "observations"),
    ]
    for operation, arguments, parameter in invalid:
        code, result = execute(operation, arguments)
        assert code == 1 and result["status"] == "error", (operation, result)
        error = result["error"]
        assert error["kind"] == "type" and "arguments" in error["message"], error
        assert error["path"] == "/$call/2/probe" and error["line"] > 0 and error["column"] > 0, error
        assert {violation["argument"] for violation in error["data"]["violations"]} == {parameter}, error
        assert any(frame.get("function") == f"/{operation}"
                   and frame.get("function_file", "").endswith("tooling/test_policy.yaml")
                   for frame in error["calls"]), error

    observation = {"suite": "golden", "failures": [], "stdout": "雪\n", "stderr": "warning\n",
                   "cases": 5, "jobs": 2, "blessed": True, "reason": "historically skipped"}
    code, result = execute("result", [observation])
    assert code == 0 and result["root"]["probe"] == {**observation, "status": "skipped"}, result
    code, result = execute("rerun", [["first"], {"results": [{"suite": "first", "status": "failed"}]}])
    assert code == 0 and result["root"]["probe"] == ["first"], result

    # Keep the actual annotations and replace only each implementation body. A
    # live interpreter must reject bad return shapes before the host bridge sees them.
    mutations = [
        ("plan", [["first"], [], False], [{"suite": "first", "action": "invented"}]),
        ("rerun", [["first"], {"results": []}], [17]),
        ("result", [{"suite": "first", "failures": []}],
         {"suite": "first", "failures": [], "status": "invented"}),
        ("report", [[]], {"command": "test", "status": "passed", "exit_code": "0",
                          "failure_count": 0, "results": []}),
    ]
    policy = yaml.safe_load(original.read_text())
    with tempfile.TemporaryDirectory(dir=h.root / "runtime", prefix="policy-annotations-") as temporary:
        for operation, arguments, bad_return in mutations:
            damaged = copy.deepcopy(policy)
            function = damaged[operation]["$call"]
            assert len(function) == 4, f"{operation} must declare its result contract"
            function[2] = {"$literal": bad_return}
            module = Path(temporary) / f"{operation}.yaml"
            module.write_text(yaml.safe_dump(damaged, sort_keys=False))
            code, result = execute(operation, arguments, module)
            assert code == 1 and result["status"] == "error", (operation, result)
            error = result["error"]
            assert error["kind"] == "type" and "in the result" in error["message"], error
            assert error["path"] == f"/{operation}/$call/2" and error["file"] == str(module), error


def verify_rerun_wiring(h):
    import tempfile
    from pathlib import Path
    import manage

    @contextlib.contextmanager
    def frozen(context, output_format):
        yield h, 0

    with tempfile.TemporaryDirectory(dir=h.root / "runtime", prefix="rerun-wiring-") as temporary:
        path = Path(temporary) / "previous.json"
        path.write_text(json.dumps(report_fixture({**suite_fixture("golden"), "blessed": True, "cases": 1})))
        with patch.object(manage, "frozen_test_context", frozen), \
             patch.object(test_runner.TestContext, "regression", return_value=0) as dispatch:
            assert manage.test(False, output_format="json", rerun_failed=str(path)) == 0
            assert dispatch.call_count == 1
            args = dispatch.call_args.args
            assert args[0] is False and args[1] == ["golden"] and args[3] is None, args
        # A broken bridge returning empty cannot turn a nonempty rerun into all suites.
        stdout = io.StringIO()
        with patch.object(manage, "frozen_test_context", frozen), \
             patch.object(test_policy, "rerun", return_value=[]), \
             patch.object(test_runner.TestContext, "regression") as dispatch, \
             contextlib.redirect_stdout(stdout):
            assert manage.test(False, output_format="json", rerun_failed=str(path)) == 1
            assert dispatch.call_count == 0
        result = json.loads(stdout.getvalue())
        assert result["results"][0]["suite"] == "orchestration" and result["failure_count"] == 1, result
