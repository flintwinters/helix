"""Repository-pinned dependency contracts, invoked by the imports suite."""
import shutil
import tempfile
from pathlib import Path

import yaml


def verify(h):
    source = h.ROOT / "tests" / "cases"
    with tempfile.TemporaryDirectory(dir=h.ROOT / "runtime", prefix="dependencies-") as directory:
        base = Path(directory)
        original = base / "original"
        original.mkdir()
        shutil.copy2(source / "import_vendored.yaml", original / "main.yaml")
        shutil.copytree(source / "modules" / "vendor", original / "modules" / "vendor")
        first = h.helix("run", str(original / "main.yaml"))
        assert first.returncode == 0, first.stdout
        root = yaml.safe_load(first.stdout)["root"]
        assert (root["old_result"], root["new_result"]) == (41, 42), root
        moved = base / "relocated"
        shutil.move(str(original), moved)
        second = h.helix("run", str(moved / "main.yaml"))
        assert second.returncode == 0 and second.stdout == first.stdout, second.stdout
        missing = moved / "modules" / "vendor" / "offset-v1" / "constants.yaml"
        missing.unlink()
        failed = h.helix("run", str(moved / "main.yaml"))
        assert failed.returncode == 1, failed.stdout
        assert h.malformed_state(failed.stdout, failed.returncode) is None
        error = yaml.safe_load(failed.stdout)["error"]
        assert error["kind"] == "import" and error["file"].endswith("offset-v1/main.yaml"), error
        assert error["path"] == "/constants/1", error
