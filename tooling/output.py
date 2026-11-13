"""Versioned project-tool reports; language output keeps its own wire format."""
import json

DEFAULT_FORMAT = "text"


def emit(report, *, stream=None):
    print(json.dumps({"version": 1, **report}, ensure_ascii=False, indent=2), file=stream)


def observed_test_status(result):
    """Status implied by independently captured suite failures and skip metadata."""
    return "failed" if result["failures"] else "skipped" if "reason" in result else "passed"


def test_report(results, *, output_format=DEFAULT_FORMAT, rendered_results=0):
    """Bootstrap/emergency reporting when no trustworthy Helix policy is available."""

    failures = [failure for result in results for failure in result["failures"]]
    code = int(bool(failures))
    return render_test_report({"command": "test", "status": "failed" if code else "passed", "exit_code": code,
                               "failure_count": len(failures), "results": results}, output_format=output_format,
                              rendered_results=rendered_results)


def render_test_result(result):
    """Print and flush one completed suite's existing text evidence."""
    import sys

    details = [f"{result['cases']} cases"] if "cases" in result else []
    if result.get("blessed"):
        details.append("expectations rewritten")
    if result.get("reason"):
        details.append(result["reason"])
    suffix = f" ({'; '.join(details)})" if details else ""
    print(f"{result['suite']}: {result['status']}{suffix}")
    # Successful chatter stays available in the explicit JSON report.
    # Warnings on stderr and all failing-suite evidence remain visible.
    for channel in ("stdout", "stderr"):
        if result.get(channel) and (result["failures"] or channel == "stderr"):
            print(result[channel], end="", file=sys.stderr if channel == "stderr" else sys.stdout)
    for failure in result["failures"]:
        print("FAIL", failure, file=sys.stderr)
    sys.stdout.flush()
    sys.stderr.flush()


def render_test_report(report, *, output_format=DEFAULT_FORMAT, rendered_results=0):
    """Render a decided report, omitting only text rows already shown as progress."""
    results = report["results"]
    code = report["exit_code"]
    if output_format == "json":
        emit(report)
    else:
        for result in results[rendered_results:]:
            render_test_result(result)
        skipped = sum(result["status"] == "skipped" for result in results)
        print(f"test: {'failed' if code else 'passed'} ({len(results)} suites, "
              f"{skipped} skipped, {report['failure_count']} failures)")
    return code
