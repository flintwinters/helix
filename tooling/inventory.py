"""Host observation and invocation for the auxiliary Helix inventory report."""
import subprocess
import sys
from pathlib import Path

import yaml


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
    if result.returncode:
        print(result.stdout or result.stderr, file=sys.stderr, end="")
        return 1
    state = yaml.safe_load(result.stdout)
    print(yaml.safe_dump(state["root"]["report"], allow_unicode=True, sort_keys=False), end="")
    return 0
