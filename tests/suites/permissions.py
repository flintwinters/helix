"""Import capabilities at the public CLI boundary, including canonical path escapes."""
import tempfile
from pathlib import Path

import yaml


def verify(h):
    with tempfile.TemporaryDirectory(dir=h.ROOT / "runtime", prefix="permissions-") as directory:
        base = Path(directory)
        allowed = base / "allowed"
        allowed.mkdir()
        sibling = base / "allowed-sibling"
        sibling.mkdir()
        (allowed / "local.yaml").write_text("value: 42\n")
        (sibling / "secret.yaml").write_text("value: 99\n")
        main = allowed / "main.yaml"
        def run(source, root, code):
            main.write_text(source)
            result = h.helix("sandbox", str(root), str(main))
            assert h.malformed_state(result.stdout, result.returncode) is None, result
            assert result.returncode == code, result
            state = yaml.safe_load(result.stdout)
            if code:
                error = state["error"]
                assert error["kind"] == "permission", error
                assert error["path"].endswith("/1") and error["line"] >= 1, error
            return state
        run("m: [import, std:math]\nr: [m.square, 3]", "-", 0)
        run("m: [import, local.yaml]", "-", 1)
        assert run("m: [import, local.yaml]", allowed, 0)["root"]["m"]["value"] == 42
        run("m: [import, ../allowed-sibling/secret.yaml]", allowed, 1)
        run(f'm: [import, "{sibling / "secret.yaml"}"]', allowed, 1)
        (allowed / "escape.yaml").symlink_to(sibling / "secret.yaml")
        run("m: [import, escape.yaml]", allowed, 1)
        (allowed / "local.yaml").write_text("m: [import, ../allowed-sibling/secret.yaml]")
        error = run("m: [import, local.yaml]", allowed, 1)["error"]
        assert error["file"].endswith("local.yaml"), error
        # Literal imports are linked even in branches/functions that are never called.
        run("f: [fn, [], [import, ../allowed-sibling/secret.yaml]]", allowed, 1)
        # Each launch has its own policy; a prior allowed launch cannot grant later access.
        (allowed / "local.yaml").write_text("42")
        run("m: [import, local.yaml]", allowed, 0)
        run("m: [import, local.yaml]", "-", 1)
