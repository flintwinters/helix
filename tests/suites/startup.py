"""Discovery import boundaries, exercised without compiling or running Helix."""
import subprocess
import sys


def verify(h):
    # Assert the import boundary rather than a machine-dependent timing limit.
    for args in [(), ("--help",), ("--help-json",), ("runtime", "--help"),
                 ("test", "--list"), ("quality", "test", "--describe")]:
        profiled = subprocess.run(
            [sys.executable, "-X", "importtime", str(h.ROOT / "manage.py"), *args],
            cwd=h.ROOT, capture_output=True, text=True, timeout=10,
        )
        assert profiled.returncode == 0, profiled.stderr
        imported = {line.rsplit("|", 1)[-1].strip() for line in profiled.stderr.splitlines()
                    if line.startswith("import time:")}
        assert not imported & {"subprocess", "tempfile", "difflib", "shlex", "yaml"}, imported
