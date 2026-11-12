"""Bounded case concurrency; suite-level harness mutations remain serial."""


def collect_cases(check, cases, jobs):
    """Run independent cases, preserving input order in the collected failures."""
    if jobs < 1:
        raise ValueError("jobs must be positive")
    if jobs == 1:
        return [failure for case in cases for failure in check(case)]

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return [failure for failures in pool.map(check, cases) for failure in failures]
