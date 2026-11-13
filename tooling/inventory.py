"""Host observation and invocation for the auxiliary Helix inventory report."""
import subprocess
import sys
from pathlib import Path

import yaml

from tooling.test_reports import failure
from tooling.test_runner import malformed_state


def _report_problem(value, records):
    """Check preservation of host observations without recomputing grouping policy."""
    if not isinstance(value, list):
        return "inventory report must be a list"
    observed = {record["path"]: record["directory"] for record in records}
    directories, paths = set(), set()
    for row in value:
        if (not isinstance(row, dict) or set(row) != {"directory", "count", "files"}
                or not isinstance(row["directory"], str) or row["directory"] in directories
                or not isinstance(row["files"], list) or not row["files"]
                or type(row["count"]) is not int or row["count"] != len(row["files"])):
            return "inventory report has an invalid directory row"
        directories.add(row["directory"])
        for path in row["files"]:
            if (not isinstance(path, str) or path in paths
                    or path not in observed or observed[path] != row["directory"]):
                return "inventory report changed or duplicated an observed file"
            paths.add(path)
    if paths != observed.keys():
        return "inventory report omitted observed files"
    return None


def report(helix, records):
    """Run the same pure shim used by golden cases; retain replayable source and state."""
    source = yaml.safe_dump({
        "shim": {"$call": ["import", str(Path(__file__).with_suffix(".yaml").resolve())]},
        "files": {"$literal": records},
        "report": {"$call": ["shim.report", {"$ref": "files"}]},
    }, allow_unicode=True, sort_keys=False)
    result = helix("run", "-", stdin=source)
    return source, result


def inventory(helix, root: Path) -> int:
    observed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root, capture_output=True, text=True, check=True,
    )
    paths = sorted(set(path for path in observed.stdout.split("\0")
                       if Path(path).parts and Path(path).parts[0] in {"src", "std", "tooling"}
                       and (root / path).is_file()))
    records = [{"path": path, "directory": str(Path(path).parent)} for path in paths]
    source, result = report(helix, records)
    artifacts = root / "runtime"
    artifacts.mkdir(exist_ok=True)
    (artifacts / "inventory.input.yaml").write_text(source)
    (artifacts / "inventory.state.yaml").write_text(result.stdout)
    (artifacts / "inventory.stderr.txt").write_text(result.stderr)
    problem = malformed_state(result.stdout, result.returncode)
    if problem is None and (result.returncode != 0 or result.stderr):
        problem = "inventory policy did not finish cleanly"
    if problem is None:
        state = yaml.safe_load(result.stdout)
        if state["status"] != "finished" or not isinstance(state["root"], dict) or "report" not in state["root"]:
            problem = "inventory policy must finish with root.report"
        else:
            problem = _report_problem(state["root"]["report"], records)
    if problem is not None:
        print(failure(root, root / "tooling" / "inventory.yaml", "inventory", problem,
                      "run", result, source=source), file=sys.stderr)
        return 1
    print(yaml.safe_dump(state["root"]["report"], allow_unicode=True, sort_keys=False), end="")
    return 0
