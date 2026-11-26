"""Faster evaluation tiers must export the same YAML state as stepwise evaluation.

Every corpus program is snapshotted to completion with regions enabled and with
`HELIX_NO_FAST` set. The exported states (evidence rows, budgets, environment,
memo) and exit codes must be identical, so a fast tier never changes what
`read` returns. Fixtures under tests/fixtures/fast cover each region shape."""
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor

STEPS = "5000000"


def snapshot(h, program, fast):
    env = {k: v for k, v in os.environ.items() if k != "HELIX_NO_FAST"}
    if not fast:
        env["HELIX_NO_FAST"] = "1"
    result = subprocess.run([str(h.root / h.binary), "snapshot", str(program), "--steps", STEPS],
                            cwd=h.root, capture_output=True, text=True, env=env, timeout=h.timeout * 6)
    return result.returncode, result.stdout, result.stderr


def verify(h):
    cases = sorted(p for p in (h.root / "tests" / "cases").glob("*.yaml") if ".expected." not in p.name)
    cases += sorted((h.root / "tests" / "fixtures" / "fast").glob("*.yaml"))
    assert cases, "no lowering corpus"
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        pairs = list(pool.map(lambda p: (p, snapshot(h, p, True), snapshot(h, p, False)), cases))
    different = [p.name for p, fast, slow in pairs if fast != slow]
    assert not different, (
        f"{len(different)} of {len(cases)} programs export different state with regions enabled: "
        f"{different[:8]}; diff `helix snapshot FILE --steps {STEPS}` with and without HELIX_NO_FAST=1")
