"""Independent production golden-policy contracts, reached through the harness suite."""
from dataclasses import replace
from pathlib import Path
import subprocess
import tempfile
import time

import yaml


FINISHED = "status: finished\nroot: 42\n"


def verify(h):
    verify_decision_projection(h)
    with tempfile.TemporaryDirectory(dir=h.root / "runtime", prefix="golden-policy-") as temporary:
        case = Path(temporary) / "probe.yaml"
        case.write_text("42\n")
        expected_path = case.with_suffix(".expected.yaml")

        def exercise(*, output=FINISHED, code=0, expected=FINISHED, repeated=None,
                     replay=None, bless=False, policy_fault=None, fault_after=0):
            if expected is None:
                expected_path.unlink(missing_ok=True)
            else:
                expected_path.write_text(expected)
            calls = {"file": 0, "replay": 0, "policy": 0}
            initial = (code, output, "")

            def runner(args, **kwargs):
                source = kwargs.get("input")
                if source is None:
                    calls["file"] += 1
                    response = initial if calls["file"] == 1 or repeated is None else repeated
                elif source.startswith("$literal:\n"):
                    calls["replay"] += 1
                    response = initial if replay is None else replay
                else:
                    calls["policy"] += 1
                    if policy_fault is None or calls["policy"] <= fault_after:
                        return h.process_runner(args, **kwargs)
                    if policy_fault == "timeout":
                        raise subprocess.TimeoutExpired(args, kwargs["timeout"])
                    response = policy_fault
                return subprocess.CompletedProcess(args, *response)

            context = replace(h, commands={case.parent: "run"}, process_runner=runner)
            failures = context.golden_case(case, bless)
            return failures, calls

        def stage(failures, expected):
            assert len(failures) == 1 and f"[{expected}]" in failures[0], failures

        failures, calls = exercise()
        assert failures == [] and calls["replay"] == 1 and calls["policy"] > 0, (failures, calls)
        # Exact presentation is a contract even when YAML meaning is unchanged.
        for expected in (FINISHED.rstrip("\n"), "status: finished\nroot: 0x2a\n", None):
            failures, calls = exercise(expected=expected)
            stage(failures, "golden")
            assert calls["replay"] == 0
        for repeated in ((0, FINISHED.replace("42", "43"), ""), (0, FINISHED, "different stderr")):
            failures, calls = exercise(expected="wrong", repeated=repeated, bless=True)
            stage(failures, "determinism")
            assert calls["replay"] == 0 and expected_path.read_text() == "wrong"
        failures, calls = exercise(expected="wrong", bless=True)
        assert failures == [] and calls["replay"] == 1 and expected_path.read_text() == FINISHED
        for replay in ((0, FINISHED.replace("42", "43"), ""), (124, "", "timeout"), (0, "[", "")):
            failures, _ = exercise(replay=replay)
            stage(failures, "roundtrip")
        # A genuine roundtrip failure still permits explicitly requested blessing
        # of the original deterministic output; policy corruption does not.
        failures, _ = exercise(expected="wrong", bless=True,
                               replay=(0, FINISHED.replace("42", "43"), ""))
        stage(failures, "roundtrip")
        assert expected_path.read_text() == FINISHED
        for code, output in (
            (1, "status: error\nerror: {kind: type, message: bad, line: 1, column: 1}\n"),
            (1, "status: signaled\npending_effect: {id: 0, operation: read, payload: null}\n"),
            (0, "status: finished\nroot: {ref: function1}\nobjects: {}\n"),
        ):
            failures, calls = exercise(output=output, code=code, expected=output)
            assert failures == [] and calls["replay"] == 0, (failures, calls)
        failures, calls = exercise(code=-11, output="", expected="wrong", bless=True)
        stage(failures, "state")
        assert calls["policy"] == calls["replay"] == 0 and expected_path.read_text() == "wrong"

        invalid_roots = [None, [], {}, {"stage": "passed", "bless": False},
                         {"stage": "unknown", "bless": False, "replay": False},
                         {"stage": "passed", "bless": 1, "replay": False},
                         {"stage": "passed", "bless": False, "replay": []}]
        faults = [(0, "", ""), (0, "[", ""), (-11, "", ""), (124, "", ""),
                  (0, "status: finished\nroot: {}\n", "unexpected stderr"), "timeout"]
        faults.extend((0, yaml.safe_dump({"status": "finished", "root": root}), "")
                      for root in invalid_roots)
        for fault in faults:
            failures, calls = exercise(expected="unchanged", bless=True, policy_fault=fault)
            stage(failures, "policy")
            assert calls["replay"] == 0 and expected_path.read_text() == "unchanged", fault
        failures, calls = exercise(expected="unchanged", bless=True,
                                   policy_fault=(-11, "", ""), fault_after=1)
        stage(failures, "policy")
        assert calls["replay"] == 1 and expected_path.read_text() == "unchanged"

        def decision(stage="passed", bless=False, replay=False):
            return (0, yaml.safe_dump({"status": "finished", "root": {
                "stage": stage, "bless": bless, "replay": replay}}), "")

        # Well-typed plans still cannot authorize impossible or unrequested actions.
        for fault, options in (
            (decision(), {}),  # A passing ordinary root must request replay.
            (decision(stage="roundtrip"), {}),  # No replay has happened yet.
            (decision(stage="determinism", bless=True), {"bless": True}),
            (decision(stage="golden", bless=True), {"bless": True}),
            (decision(bless=True, replay=True), {}),  # No --bless authorization.
            (decision(replay=True), {"fault_after": 1}),  # Replay already completed.
        ):
            failures, _ = exercise(policy_fault=fault, **options)
            stage(failures, "policy")
            assert expected_path.read_text() == FINISHED

        # These decisions have valid shapes and plausible phase actions, but lie
        # about independent host observations. None may authorize replay or writes.
        error_state = "status: error\nerror: {kind: type, message: bad, line: 1, column: 1}\n"
        for repeated in ((0, FINISHED.replace("42", "43"), ""),
                         (0, FINISHED, "different stderr"), (1, error_state, "")):
            failures, calls = exercise(expected="unchanged", repeated=repeated, bless=True,
                                       policy_fault=decision(bless=True, replay=True))
            stage(failures, "policy")
            assert calls["replay"] == 0 and expected_path.read_text() == "unchanged", calls
        # Error states have no ordinary root to replay. A forged pass here would
        # otherwise finish successfully and bless differing outputs immediately.
        failures, calls = exercise(output=error_state, code=1, expected="unchanged",
                                   repeated=(1, error_state.replace("message: bad", "message: different"), ""),
                                   bless=True, policy_fault=decision(bless=True))
        stage(failures, "policy")
        assert calls["policy"] == 1 and calls["replay"] == 0, calls
        assert expected_path.read_text() == "unchanged"
        for expected in (None, "wrong", FINISHED.rstrip("\n")):
            failures, calls = exercise(expected=expected, policy_fault=decision(replay=True))
            stage(failures, "policy")
            assert calls["replay"] == 0, calls
            assert (expected_path.read_text() if expected_path.exists() else None) == expected

        # Permission to bless is not evidence that the policy will actually fix
        # an absent or different expectation before claiming success.
        for expected in (None, "wrong"):
            for late in (False, True):
                failures, calls = exercise(expected=expected, bless=True,
                                           policy_fault=decision(replay=not late), fault_after=int(late))
                stage(failures, "policy")
                assert calls["replay"] == int(late) and calls["policy"] == 1 + int(late), calls
                assert (expected_path.read_text() if expected_path.exists() else None) == expected

        for replay in ((0, FINISHED.replace("42", "43"), ""), (1, error_state, "")):
            failures, calls = exercise(expected="unchanged", bless=True, replay=replay,
                                       policy_fault=decision(bless=True), fault_after=1)
            stage(failures, "policy")
            assert calls["replay"] == 1 and calls["policy"] == 2, calls
            assert expected_path.read_text() == "unchanged"

        # Even a plausible false pass from a damaged interpreter cannot hide a
        # host-observed crash, timeout or malformed replay state.
        for replay in ((-11, "", ""), (124, "", "timeout"), (0, "[", "")):
            failures, calls = exercise(replay=replay, policy_fault=decision(), fault_after=1)
            stage(failures, "policy")
            assert calls["replay"] == 1 and calls["policy"] == 2

        # Exercise a real large corpus observation, not only tiny synthetic outputs.
        largest = max((path for directory in h.commands
                       for path in directory.glob("*.expected.yaml")), key=lambda path: path.stat().st_size)
        output = largest.read_text()
        code = 0 if yaml.safe_load(output)["status"] == "finished" else 1
        started = time.perf_counter()
        failures, calls = exercise(output=output, code=code, expected=output)
        assert failures == [], failures
        print(f"golden_policy: fixture={largest.relative_to(h.root)} characters={len(output)} "
              f"policy_calls={calls['policy']} seconds={time.perf_counter() - started:.3f}")


def verify_decision_projection(h):
    """The full compatibility API and host facts share one independently checked policy."""
    from itertools import product

    cases = []
    for deterministic, matches, bless, replayable, replay_kind in product(
            (False, True), (False, True), (False, True), (False, True),
            ('absent', 'matching', 'wrong-root', 'failed')):
        root = '42\n' if replayable else None
        run = {'returncode': 0, 'stdout': FINISHED, 'stderr': ''}
        repeated = {**run, 'stderr': '' if deterministic else 'different'}
        replay = (None if replay_kind == 'absent' else {
            'returncode': 1 if replay_kind == 'failed' else 0,
            'root': '43\n' if replay_kind == 'wrong-root' else root})
        observation = {'run': run, 'repeated': repeated, 'expected': FINISHED if matches else None,
                       'bless': bless, 'root': root, 'replay': replay}
        facts = {'deterministic': deterministic, 'matches_expected': matches,
                 'bless_requested': bless, 'replayable': replayable,
                 'replay_completed': replay is not None,
                 'replay_matches': replay_kind == 'matching'}
        accepted = deterministic and (matches or bless)
        stage = ('determinism' if not deterministic else 'golden' if not accepted else
                 'roundtrip' if replayable and replay is not None and replay_kind != 'matching' else 'passed')
        expected = {'stage': stage, 'bless': deterministic and bless,
                    'replay': accepted and replayable and replay is None}
        cases.append((observation, facts, expected))

    def call(actor, *args):
        return {'$call': [actor, *args]}

    program = call('let', {'test': call('import', 'std:test')}, [
        [call('test.golden_plan', {'$literal': observation}),
         call('test.golden_decision', {'$literal': facts})]
        for observation, facts, _ in cases])
    result = h.helix('run', '-', stdin=yaml.safe_dump(program))
    assert result.returncode == 0 and not result.stderr, result
    assert yaml.safe_load(result.stdout) == {'status': 'finished', 'root': [
        [expected, expected] for _, _, expected in cases]}, result.stdout

    for observation, facts, expected in cases:
        # The full API can describe combinations the host lifecycle never emits;
        # its independent replay veto remains stricter than policy selection.
        if not facts['replayable'] and facts['replay_completed']:
            continue
        def runner(args, **kwargs):
            source = yaml.safe_load(kwargs['input'])
            invocation = source['$call'][2]['$call']
            assert invocation[0] == 'test.golden_decision', invocation
            projected = invocation[1]['$literal']
            assert projected == facts and all(type(value) is bool for value in projected.values()), projected
            return subprocess.CompletedProcess(args, 0, yaml.safe_dump({
                'status': 'finished', 'root': expected}), '')
        assert replace(h, process_runner=runner).golden_policy(observation) == expected
