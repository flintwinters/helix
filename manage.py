#!/usr/bin/env python3
"""Single entrypoint for building, running, and testing Helix.

    ./manage.py build               cargo build
    ./manage.py run FILE            run a program, print its serialized VM state
    ./manage.py lint [ARGS...]      structural lint via the hyperlinter submodule (JSON; pass --format=text for people)
    ./manage.py test [--bless]      golden-test every tests/cases/*.yaml (--bless rewrites expectations)

Golden cases: tests/cases/NAME.yaml is the program, NAME.expected.yaml the exact serialized state.
Finished cases are also checked for round-trip stability: feeding the emitted `root` back in as a
program must reproduce the same `root`.
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "tests" / "cases"
BIN = ROOT / "target" / "debug" / "helix"
HYPERLINTER = ROOT / "hyperlinter"


def build() -> int:
    return subprocess.call(["cargo", "build", "--quiet"], cwd=ROOT)


def lint(extra: list[str]) -> int:
    """Run hyperlinter from the host root (required so it loads this repo's Cargo.toml and baseline)."""
    tsx = HYPERLINTER / "node_modules" / ".bin" / "tsx"
    if not tsx.exists():
        subprocess.check_call(["git", "submodule", "update", "--init", "hyperlinter"], cwd=ROOT)
        subprocess.check_call(["npm", "--prefix", str(HYPERLINTER), "ci", "--include=dev"], cwd=ROOT)
    return subprocess.call([str(tsx), str(HYPERLINTER / "src" / "cli.ts"), *extra], cwd=ROOT)


def helix(*args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([str(BIN), *args], input=stdin, capture_output=True, text=True, cwd=ROOT)


def root_section(state: str) -> str | None:
    """Dedented body of the `root:` block of a finished state, or None."""
    lines = state.splitlines()
    if not lines or lines[0] != "status: finished":
        return None
    body = lines[lines.index("root:") + 1 :]
    return "\n".join(l[2:] for l in body) + "\n"


def test(bless: bool) -> int:
    if (code := build()) != 0:
        return code
    cases = sorted(CASES.glob("*.yaml"))
    cases = [c for c in cases if not c.name.endswith(".expected.yaml")]
    failures = []
    for case in cases:
        expected_path = case.with_suffix(".expected.yaml")
        out = helix("run", str(case.relative_to(ROOT))).stdout
        if bless:
            expected_path.write_text(out)
        elif not expected_path.exists() or expected_path.read_text() != out:
            failures.append(f"{case.name}: state differs from {expected_path.name}\n--- got\n{out}")
            continue
        if (program := root_section(out)) is not None:
            again = helix("run", "-", stdin=program).stdout
            if root_section(again) != program:
                failures.append(f"{case.name}: round trip is not stable\n--- first\n{program}--- second\n{again}")
    for f in failures:
        print("FAIL", f, file=sys.stderr)
    print(f"{len(cases) - len(failures)}/{len(cases)} cases passed")
    return 1 if failures else 0


def main() -> int:
    if sys.argv[1:2] == ["lint"]:  # forward everything verbatim, including flags argparse would reject
        return lint(sys.argv[2:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sub.add_parser("run").add_argument("file")
    sub.add_parser("test").add_argument("--bless", action="store_true")
    sub.add_parser("lint", help="forwards all remaining args to hyperlinter")
    args = ap.parse_args()
    if args.cmd == "build":
        return build()
    if args.cmd == "run":
        return build() or subprocess.call([str(BIN), "run", args.file], cwd=ROOT)
    return test(args.bless)


if __name__ == "__main__":
    sys.exit(main())
