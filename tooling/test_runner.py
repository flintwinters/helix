"""Bounded case concurrency; suite-level harness mutations remain serial."""


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
