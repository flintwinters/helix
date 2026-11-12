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

    cases = [case for directory in directories for case in sorted(directory.glob("*.yaml"))
             if not case.name.endswith(".expected.yaml")]
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

    def helix(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        command = [str(self.binary), *args]
        try:
            return self.process_runner(command, input=stdin, capture_output=True, text=True,
                                       cwd=self.root, timeout=self.timeout)
        except subprocess.TimeoutExpired as error:
            return subprocess.CompletedProcess(command, 124, "", f"timed out: {error}")

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
            run = self.helix(*shlex.split(command), "-", stdin=variant)
            if (why := self.malformed_state(run.stdout, run.returncode)) is not None:
                return [test_failure(self.root, case, "robustness", why, command, run, source=variant)]
        return []

    def robustness(self, cases: list[Path], jobs: int = DEFAULT_TEST_JOBS) -> list[str]:
        return collect_cases(self.robustness_case, cases, jobs)

    def golden_case(self, case: Path, bless: bool) -> list[str]:
        failures = []
        expected_path = case.with_suffix(".expected.yaml")
        command = self.commands[case.parent]
        run = self.helix(*shlex.split(command), str(case.relative_to(self.root)))
        out = run.stdout
        if (why := self.malformed_state(out, run.returncode)) is not None:
            failures.append(test_failure(self.root, case, "state", why, command, run))
            return failures
        repeated = self.helix(*shlex.split(command), str(case.relative_to(self.root)))
        if (repeated.returncode, repeated.stdout, repeated.stderr) != (run.returncode, out, run.stderr):
            failures.append(test_failure(self.root, case, "determinism", "two runs differ", command, repeated, expected=out))
            return failures
        if bless:
            expected_path.write_text(out)
        elif not expected_path.exists() or expected_path.read_text() != out:
            failures.append(test_failure(self.root, case, "golden", f"state differs from {expected_path.relative_to(self.root)}",
                                         command, run, expected=expected_path.read_text() if expected_path.exists() else ""))
            return failures
        if (program := self.root_section(out)) is not None:
            from textwrap import indent

            replay = "$literal:\n" + indent(program, "  ")
            reloaded = self.helix("run", "-", stdin=replay)
            if self.root_section(reloaded.stdout) != program or reloaded.returncode != 0:
                failures.append(test_failure(self.root, case, "roundtrip", "emitted root is not stable",
                                             "run", reloaded, source=replay, expected=out))
        return failures

    def regression(self, bless: bool, suites: list[str] | None, jobs: int = DEFAULT_TEST_JOBS,
                   cases: list[Path] | None = None, output_format: str = DEFAULT_FORMAT) -> int:
        import contextlib
        import io
        from tooling.output import test_report

        selected = set(suites or SUITES)
        results = []

        def record(name, check, **details):
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                try:
                    failures = check()
                except (Exception, SystemExit):
                    import traceback
                    failures = [traceback.format_exc()]
            results.append({"suite": name, "status": "failed" if failures else "passed",
                            "failures": failures, "stdout": stdout.getvalue(), "stderr": stderr.getvalue(),
                            **details})

        def library():
            from manage import build_lock
            with build_lock():
                result = subprocess.run(["cargo", "test", "--quiet", "--lib", "--test", "vm"], cwd=self.root, timeout=120,
                                        capture_output=True, text=True)
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
            return ["library: cargo test failed"] if result.returncode else []

        for name in (name for name in SUITES if name in selected - {"golden", "robustness"}):
            if name == "library":
                record(name, library)
            else:
                from tests.suites import contracts
                record(name, lambda: contracts.run(name, self))
        if not selected & {"golden", "robustness"}:
            return test_report(results, output_format=output_format)
        if cases is None:
            cases = discover_cases(self.commands, self.root)
        record("standard_library", self.check_standard_library)
        if "golden" in selected:
            from functools import partial

            record("golden", lambda: collect_cases(partial(self.golden_case, bless=bless), cases, jobs),
                   cases=len(cases), jobs=jobs, blessed=bless)
        if "robustness" in selected:
            if bless:
                results.append({"suite": "robustness", "status": "skipped", "failures": [],
                                "reason": "disabled while blessing expectations"})
            else:
                record("robustness", lambda: self.robustness(cases, jobs), cases=len(cases), jobs=jobs)
        return test_report(results, output_format=output_format)

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
