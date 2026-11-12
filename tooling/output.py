"""Versioned project-tool reports; language output keeps its own wire format."""
import json


def emit(report, *, stream=None):
    print(json.dumps({"version": 1, **report}, ensure_ascii=False, indent=2), file=stream)


def test_report(results, *, output_format="json"):
    import sys

    failures = [failure for result in results for failure in result["failures"]]
    code = int(bool(failures))
    if output_format == "json":
        emit({"command": "test", "status": "failed" if code else "passed", "exit_code": code,
              "failure_count": len(failures), "results": results})
    else:
        for result in results:
            print(f"{result['suite']}: {result['status']}")
            for channel in ("stdout", "stderr"):
                if result.get(channel):
                    print(result[channel], end="", file=sys.stderr if channel == "stderr" else sys.stdout)
        for failure in failures:
            print("FAIL", failure, file=sys.stderr)
    return code
