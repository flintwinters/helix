"""Versioned project-tool reports; language output keeps its own wire format."""
import json

DEFAULT_FORMAT = "text"


def emit(report, *, stream=None):
    print(json.dumps({"version": 1, **report}, ensure_ascii=False, indent=2), file=stream)


def test_report(results, *, output_format=DEFAULT_FORMAT):
    import sys

    failures = [failure for result in results for failure in result["failures"]]
    code = int(bool(failures))
    if output_format == "json":
        emit({"command": "test", "status": "failed" if code else "passed", "exit_code": code,
              "failure_count": len(failures), "results": results})
    else:
        for result in results:
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
        skipped = sum(result["status"] == "skipped" for result in results)
        print(f"test: {'failed' if code else 'passed'} ({len(results)} suites, "
              f"{skipped} skipped, {len(failures)} failures)")
    return code
