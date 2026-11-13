"""Explicit test execution contexts, fixture selection and bounded case concurrency."""
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from tooling.cli import DEFAULT_TEST_JOBS, SUITES
from tooling.output import DEFAULT_FORMAT
from tooling.test_reports import failure as test_failure


def discover_cases(directories, root, patterns=None):
    """Select fixture stems or root-relative paths; every requested glob must match."""
    from fnmatch import fnmatchcase

    if not directories:
        raise ValueError("no golden/robustness fixture directories configured")
    cases = []
    for directory in directories:
        if not directory.is_dir():
            raise ValueError(f"fixture directory is missing or not a directory: {directory}")
        discovered = [case for case in sorted(directory.glob("*.yaml"))
                      if not case.name.endswith(".expected.yaml") and case.is_file()]
        if not discovered:
            raise ValueError(f"fixture directory contains no runnable YAML inputs: {directory}")
        cases.extend(discovered)
    if not patterns:
        return cases
    selected = set()
    for pattern in patterns:
        matches = {case for case in cases if fnmatchcase(case.stem, pattern)
                   or fnmatchcase(case.relative_to(root).as_posix(), pattern)}
        if not matches:
            raise ValueError(f"--case {pattern!r} matched no golden/robustness inputs")
        selected.update(matches)
    return [case for case in cases if case in selected]


def collect_cases(check, cases, jobs):
    """Run independent cases, preserving input order in the collected failures."""
    if jobs < 1:
        raise ValueError("jobs must be positive")
    if jobs == 1:
        return [failure for case in cases for failure in check(case)]

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return [failure for failures in pool.map(check, cases) for failure in failures]


def root_section(state: str) -> str | None:
    """Extract ordinary root YAML for literal replay; object graphs are inspection only."""
    import yaml

    # Unicode line separators inside quoted scalars are not YAML physical lines.
    lines = state.split("\n")
    value = yaml.safe_load(state)
    if not isinstance(value, dict) or value.get("status") != "finished" or "objects" in value:
        return None
    index = next((i for i, line in enumerate(lines)
                  if line == "root:" or line.startswith("root: ")), None)
    if index is None:
        return None
    inline = lines[index][len("root:"):].lstrip()
    body = [inline] if inline else []
    for line in lines[index + 1 :]:
        if not line.startswith("  "):
            break
        body.append(line if inline else line[2:])
    return "\n".join(body) + "\n"


ERROR_FIELDS = {"kind", "message", "line", "column"}  # `path` is omitted for errors outside the graph (parse)


class GoldenPolicyError(RuntimeError):
    """A failed policy execution, retaining the exact replayable evidence."""

    def __init__(self, reason, result, source):
        super().__init__(f"std:test.golden_plan failed: {reason}")
        self.result = result
        self.source = source


def malformed_state(out: str, returncode: int) -> str | None:
    """Why `out` is not a well-formed serialized state (None if it is): the contract that nothing panics
    and every error is structured, machine-parseable data."""
    import yaml

    if returncode not in (0, 1):
        return f"exit code {returncode} (panic?)"
    try:
        state = yaml.load(out, Loader=getattr(yaml, "CSafeLoader", yaml.SafeLoader))
    except yaml.YAMLError as e:
        return f"state is not valid YAML: {e}"
    if not isinstance(state, dict) or "status" not in state:
        return "state has no `status`"
    if state["status"] not in ("finished", "error", "signaled"):
        return f"unexpected terminal status {state['status']!r}"
    if (state["status"] in ("error", "signaled")) != (returncode == 1):
        return "exit code disagrees with status"
    if state["status"] == "signaled":
        pending = state.get("pending_effect")
        if not isinstance(pending, dict) or not {"id", "operation", "payload"} <= pending.keys():
            return "signaled state has no explicit pending effect"
    if state["status"] == "finished" and "root" not in state:
        return "finished state has no root"
    if state["status"] == "error":
        errors = state.get("errors") if "errors" in state else [state.get("error")]
        if not (isinstance(errors, list) and errors and all(isinstance(e, dict) and ERROR_FIELDS <= e.keys() for e in errors)):
            return f"error lacks fields {sorted(ERROR_FIELDS)}"
    return None


@dataclass(frozen=True)
class TestContext:
    """One test run's execution configuration; derive isolated overrides with replace()."""

    root: Path
    binary: Path
    timeout: float = 10
    commands: Mapping[Path, str] | None = None
    process_runner: Callable = field(default=subprocess.run, repr=False, compare=False)

    def __post_init__(self):
        commands = self.commands if self.commands is not None else {
            self.root / "tests" / "cases": "run", self.root / "tests" / "check": "check --evaluate",
        }
        object.__setattr__(self, "commands", MappingProxyType(dict(commands)))

    root_section = staticmethod(root_section)
    malformed_state = staticmethod(malformed_state)

    def helix(self, *args: str, stdin: str | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
        command = [str(self.root / self.binary), *args]
        try:
            return self.process_runner(command, input=stdin, capture_output=True, text=True,
                                       cwd=self.root if cwd is None else cwd, timeout=self.timeout)
        except subprocess.TimeoutExpired as error:
            # TimeoutExpired may carry bytes even with text=True, including a
            # partial UTF-8 sequence. Preserve diagnostic text without letting
            # decoding hide the timeout or turn partial output into success.
            def captured_text(value):
                return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else value or ''

            stdout, stderr = captured_text(error.output), captured_text(error.stderr)
            separator = '\n' if stderr and not stderr.endswith('\n') else ''
            return subprocess.CompletedProcess(command, 124, stdout, f"{stderr}{separator}timed out: {error}")
        except OSError as error:
            return subprocess.CompletedProcess(command, 127, '',
                f"could not launch {shlex.join(command)}: {error}\n")

    def mutations(self, source: str):
        """Generate damaged inputs in Helix, keeping generator failures host-visible."""
        import yaml

        program = yaml.safe_dump({"$call": ["let", {
            "fuzz": {"$call": ["import", "std:fuzz"]},
        }, {"$call": ["fuzz.plan", source]}]}, allow_unicode=False)
        result = self.helix("run", "-", stdin=program)
        problem = self.malformed_state(result.stdout, result.returncode)
        if problem is None and (result.returncode != 0 or result.stderr):
            problem = "generator did not finish cleanly"
        if problem is None:
            state = yaml.safe_load(result.stdout)
            variants = state.get("root")
            if not isinstance(variants, list) or not all(
                isinstance(variant, list) and all(
                    isinstance(span, list) and len(span) == 2
                    and all(type(bound) is int for bound in span)
                    and 0 <= span[0] <= span[1] <= len(source)
                    for span in variant)
                for variant in variants
            ):
                problem = "generator root must contain variants of bounded source spans"
        if problem is not None:
            raise RuntimeError(f"std:fuzz.plan failed: {problem}\n"
                               f"exit: {result.returncode}\n{result.stdout}\n{result.stderr}")
        for variant in variants:
            yield "".join(source[start:end] for start, end in variant)

    def test(self, bless: bool, suites: list[str] | None = None, jobs: int = DEFAULT_TEST_JOBS,
             patterns: list[str] | None = None) -> int:
        """Execute selected contracts using this context's already prepared executable."""
        try:
            cases = discover_cases(self.commands, self.root, patterns) if patterns else None
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        return self.regression(bless, suites, jobs, cases)

    def robustness_case(self, case: Path) -> list[str]:
        from itertools import chain

        src = case.read_text()
        command = self.commands[case.parent]
        for variant in chain((src,), self.mutations(src)):
            run = self.helix(*shlex.split(command), "-", stdin=variant, cwd=case.parent)
            if (why := self.malformed_state(run.stdout, run.returncode)) is not None:
                return [test_failure(self.root, case, "robustness", why, command, run,
                                     source=variant, source_cwd=case.parent)]
        return []

    def robustness(self, cases: list[Path], jobs: int = DEFAULT_TEST_JOBS) -> list[str]:
        return collect_cases(self.robustness_case, cases, jobs)

    def golden_policy(self, observation):
        """Ask Helix for the next golden action; reject broken policy execution."""
        import yaml

        source = yaml.safe_dump({"$call": ["let", {
            "test": {"$call": ["import", "std:test"]},
        }, {"$call": ["test.golden_plan", {"$literal": observation}]}]}, allow_unicode=False)
        result = self.helix("run", "-", stdin=source)
        problem = self.malformed_state(result.stdout, result.returncode)
        if problem is None and (result.returncode != 0 or result.stderr):
            problem = "policy did not finish cleanly"
        if problem is None:
            decision = yaml.safe_load(result.stdout).get("root")
            if (not isinstance(decision, dict) or set(decision) != {"stage", "bless", "replay"}
                    or decision.get("stage") not in ("passed", "determinism", "golden", "roundtrip")
                    or type(decision.get("bless")) is not bool
                    or type(decision.get("replay")) is not bool):
                problem = "policy root must contain a stage and boolean bless/replay actions"
            elif (decision["bless"] and not observation["bless"]
                  or decision["replay"] and (decision["stage"] != "passed"
                      or observation["root"] is None or observation["replay"] is not None)
                  or decision["stage"] in ("determinism", "golden") and decision["bless"]
                  or decision["stage"] == "roundtrip" and observation["replay"] is None
                  or decision["stage"] == "passed" and observation["root"] is not None
                      and observation["replay"] is None and not decision["replay"]):
                problem = "policy returned actions inconsistent with the observation phase"
            # Helix selects the next action; independently captured evidence can
            # veto acceptance or persistence even when that decision is well formed.
            elif ((decision["bless"] or decision["replay"] or decision["stage"] == "passed")
                  and observation["run"] != observation["repeated"]):
                problem = "policy accepted nondeterministic process observations"
            elif ((decision["replay"] or decision["stage"] == "passed")
                  and not decision["bless"]
                  and observation["run"]["stdout"] != observation["expected"]):
                problem = "policy accepted an expectation mismatch without blessing"
            elif (decision["stage"] == "passed" and observation["replay"] is not None
                  and (observation["replay"]["returncode"] != 0
                       or observation["replay"]["root"] != observation["root"])):
                problem = "policy accepted a failed or changed replay"
        if problem is not None:
            raise GoldenPolicyError(problem, result, source)
        return decision

    def golden_case(self, case: Path, bless: bool) -> list[str]:
        expected_path = case.with_suffix(".expected.yaml")
        command = self.commands[case.parent]
        run = self.helix(*shlex.split(command), str(case.relative_to(self.root)))
        out = run.stdout
        if (why := self.malformed_state(out, run.returncode)) is not None:
            return [test_failure(self.root, case, "state", why, command, run)]
        repeated = self.helix(*shlex.split(command), str(case.relative_to(self.root)))
        if (why := self.malformed_state(repeated.stdout, repeated.returncode)) is not None:
            return [test_failure(self.root, case, "determinism", why, command, repeated, expected=out)]
        program = self.root_section(out)
        observation = {
            "run": {"returncode": run.returncode, "stdout": out, "stderr": run.stderr},
            "repeated": {"returncode": repeated.returncode, "stdout": repeated.stdout, "stderr": repeated.stderr},
            "expected": expected_path.read_text() if expected_path.exists() else None,
            "bless": bless, "root": program, "replay": None,
        }
        replay_problem = None
        try:
            decision = self.golden_policy(observation)
            if decision["replay"]:
                from textwrap import indent

                replay = "$literal:\n" + indent(program, "  ")
                reloaded = self.helix("run", "-", stdin=replay)
                replay_problem = self.malformed_state(reloaded.stdout, reloaded.returncode)
                observation["replay"] = {"returncode": reloaded.returncode,
                                         "root": self.root_section(reloaded.stdout) if replay_problem is None else None}
                decision = self.golden_policy(observation)
        except GoldenPolicyError as error:
            return [test_failure(self.root, case, "policy", str(error), "run", error.result,
                                 source=error.source)]
        # A broken policy must never overwrite an expectation. Valid roundtrip failures
        # retain the established behavior of blessing the original deterministic run.
        if decision["bless"]:
            expected_path.write_text(out)
        # Process failures remain independently observable even if the policy is broken.
        if replay_problem is not None:
            return [test_failure(self.root, case, "roundtrip", replay_problem, "run", reloaded,
                                 source=replay, expected=out)]
        match decision["stage"]:
            case "determinism":
                return [test_failure(self.root, case, "determinism", "two runs differ", command, repeated, expected=out)]
            case "golden":
                return [test_failure(self.root, case, "golden", f"state differs from {expected_path.relative_to(self.root)}",
                                     command, run, expected=observation["expected"] or "")]
            case "roundtrip":
                return [test_failure(self.root, case, "roundtrip", "emitted root is not stable",
                                     "run", reloaded, source=replay, expected=out)]
        return []

    def regression(self, bless: bool, suites: list[str] | None, jobs: int = DEFAULT_TEST_JOBS,
                   cases: list[Path] | None = None, output_format: str = DEFAULT_FORMAT, *,
                   case_selections: Mapping[str, list[Path]] | None = None) -> int:
        import contextlib
        import io
        from tooling import test_policy
        from tooling.output import observed_test_status, render_test_report, render_test_result, test_report

        # This one bootstrap contract must run without a built interpreter.
        bootstrap = bool(suites) and set(suites) == {"startup"}
        results = []
        try:
            plan = ([{"suite": "startup", "action": "run"}] if bootstrap else
                    test_policy.plan(self, SUITES, suites, bless))
        except test_policy.PolicyError as error:
            return test_report([{"suite": "orchestration", "status": "failed", "failures": [str(error)]}],
                               output_format=output_format)

        fixture_suites = [task["suite"] for task in plan
                          if task["action"] == "run" and task["suite"] in {"golden", "robustness"}]
        selected_cases = {}
        scoped_suites = set()
        if fixture_suites:
            try:
                discovered = discover_cases(self.commands, self.root)
                for name in fixture_suites:
                    selected = case_selections.get(name, cases) if case_selections is not None else cases
                    if selected is None:
                        selected_cases[name] = discovered
                    else:
                        if not selected:
                            raise ValueError("golden/robustness fixture selection must not be empty")
                        if len(set(selected)) != len(selected) or any(case not in discovered for case in selected):
                            raise ValueError(f"{name} fixture selection must contain unique discovered inputs")
                        selected_cases[name] = selected
                        scoped_suites.add(name)
            except (ValueError, OSError) as error:
                return test_report([{"suite": "orchestration", "status": "failed", "failures": [str(error)]}],
                                   output_format=output_format)

        def completed(observation):
            results.append(observation)
            if output_format != "json":
                render_test_result({**observation, "status": observed_test_status(observation)})

        def record(name, check, **details):
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                try:
                    failures = check()
                    if not isinstance(failures, list) or any(not isinstance(item, str) for item in failures):
                        raise TypeError(f"{name} suite must return a list of failure strings")
                except (Exception, SystemExit):
                    import traceback
                    failures = [traceback.format_exc()]
            completed({"suite": name, "failures": failures, "stdout": stdout.getvalue(), "stderr": stderr.getvalue(),
                       **details})

        def library():
            from manage import build_lock
            with build_lock():
                result = subprocess.run(["cargo", "test", "--quiet", "--lib", "--test", "vm"], cwd=self.root, timeout=120,
                                        capture_output=True, text=True)
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            return ["library: cargo test failed"] if result.returncode else []

        for task in plan:
            name = task["suite"]
            if task["action"] == "skip":
                completed({"suite": name, "failures": [], "reason": task["reason"]})
            elif name == "library":
                record(name, library)
            elif name == "standard_library":
                record(name, self.check_standard_library)
            elif name in {"golden", "robustness"}:
                suite_cases = selected_cases[name]
                details = {"cases": len(suite_cases), "jobs": jobs}
                if name in scoped_suites:
                    details["case_paths"] = [case.relative_to(self.root).as_posix() for case in suite_cases]
                if name == "golden":
                    from functools import partial
                    record(name, lambda: collect_cases(partial(self.golden_case, bless=bless), suite_cases, jobs),
                           **details, blessed=bless)
                else:
                    record(name, lambda: self.robustness(suite_cases, jobs), **details)
            else:
                from tests.suites import contracts
                record(name, lambda: contracts.run(name, self))
        if bootstrap:
            return test_report([{**row, "status": observed_test_status(row)} for row in results],
                               output_format=output_format, rendered_results=len(results))
        try:
            report = test_policy.report(self, results)
        except test_policy.PolicyError as error:
            # Host emergency reporting retains all observations even when the
            # interpreter responsible for aggregation crashes or lies about them.
            fallback = [{**row, "status": observed_test_status(row)} for row in results]
            fallback.append({"suite": "orchestration", "status": "failed", "failures": [str(error)]})
            return test_report(fallback, output_format=output_format, rendered_results=len(results))
        return render_test_report(report, output_format=output_format, rendered_results=len(results))

    def check_standard_library(self) -> list[str]:
        failures = []
        modules = self.helix("std")
        expected_modules = {f"std:{source.stem}" for source in (self.root / "std").glob("*.yaml")}
        discovered_modules = modules.stdout.splitlines()
        if modules.returncode != 0 or set(discovered_modules) != expected_modules or len(discovered_modules) != len(expected_modules):
            failures.append("standard library discovery failed")
        for source in sorted((self.root / "std").glob("*.yaml")):
            shown = self.helix("std", source.stem)
            if shown.returncode != 0 or shown.stdout != source.read_text():
                failures.append(f"{source.name}: standard library source differs")
        if self.helix("std", "unknown").returncode != 2:
            failures.append("unknown standard library must exit with usage error")
        return failures
