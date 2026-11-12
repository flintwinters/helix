"""Tooling bridge contracts, called by manage.py test --suite cli."""
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import yaml

from tooling.inventory import inventory, report


def verify(h):
    records = [{"path": 'src/a "quoted".rs', "directory": "src"},
               {"path": "std/雪.yaml", "directory": "std"},
               {"path": "src/second.rs", "directory": "src"}]
    source, result = report(h.helix, records)
    assert h.malformed_state(result.stdout, result.returncode) is None
    expected = [{"directory": "src", "count": 2, "files": [records[0]["path"], records[2]["path"]]},
                {"directory": "std", "count": 1, "files": [records[1]["path"]]}]
    assert yaml.safe_load(result.stdout)["root"]["report"] == expected
    assert h.helix("run", "-", stdin=source).stdout == result.stdout
    with tempfile.TemporaryDirectory(dir=h.root / "runtime", prefix="inventory-") as directory:
        replay = Path(directory) / "inventory.input.yaml"
        replay.write_text(source)
        assert h.helix("run", str(replay)).stdout == result.stdout
    assert yaml.safe_load(report(h.helix, [])[1].stdout)["root"]["report"] == []
    # The command must surface shim failure, rather than compute a host-language fallback.
    failure = subprocess.CompletedProcess([], 1, "status: error\n", "")
    import contextlib
    import io
    with patch("tooling.inventory.report", return_value=(source, failure)), contextlib.redirect_stderr(io.StringIO()):
        assert inventory(h.helix, h.root) == 1
