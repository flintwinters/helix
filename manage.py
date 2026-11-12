#!/usr/bin/env python3
"""Single entrypoint for building, running, and testing Helix.

    ./manage.py build               cargo build
    ./manage.py run FILE            run a program, print its serialized VM state
    ./manage.py std [MODULE]        list standard modules or print module source
    ./manage.py lint [ARGS...]      structural lint via the hyperlinter submodule (JSON; pass --format=text for people)
    ./manage.py test [--bless]      golden-test every tests/cases/*.yaml (--bless rewrites expectations)

Golden cases: tests/cases/NAME.yaml is the program, NAME.expected.yaml the exact serialized state.
Finished cases are also checked for round-trip stability: feeding the emitted `root` back in as a
program must reproduce the same `root`.
"""
import argparse
import subprocess
import sys
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "tests" / "cases"
COMMANDS = {CASES: "run"}
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
    try:
        return subprocess.run([str(BIN), *args], input=stdin, capture_output=True, text=True, cwd=ROOT,
                              timeout=float(os.environ.get("HELIX_TEST_TIMEOUT", "10")))
    except subprocess.TimeoutExpired as error:
        return subprocess.CompletedProcess([str(BIN), *args], 124, "", f"timed out: {error}")


def root_section(state: str) -> str | None:
    """Dedented body of the `root:` block of a finished state, or None."""
    lines = state.splitlines()
    if not lines or lines[0] != "status: finished":
        return None
    body = lines[lines.index("root:") + 1 :]
    return "\n".join(l[2:] for l in body) + "\n"


ERROR_FIELDS = {"kind", "message", "line", "column"}  # `path` is omitted for errors outside the graph (parse)


def malformed_state(out: str, returncode: int) -> str | None:
    """Why `out` is not a well-formed serialized state (None if it is): the contract that nothing panics
    and every error is structured, machine-parseable data."""
    if returncode not in (0, 1):
        return f"exit code {returncode} (panic?)"
    try:
        state = yaml.safe_load(out)
    except yaml.YAMLError as e:
        return f"state is not valid YAML: {e}"
    if not isinstance(state, dict) or "status" not in state:
        return "state has no `status`"
    if state["status"] not in ("finished", "error"):
        return f"unexpected terminal status {state['status']!r}"
    if (state["status"] == "error") != (returncode == 1):
        return "exit code disagrees with status"
    if state["status"] == "finished" and "root" not in state:
        return "finished state has no root"
    if state["status"] == "error" and not (isinstance(state.get("error"), dict) and ERROR_FIELDS <= state["error"].keys()):
        return f"error lacks fields {sorted(ERROR_FIELDS)}"
    return None


def mutations(src: str):
    """Damaged variants of a program: every line prefix, every single-line deletion, and strided character prefixes."""
    lines = src.splitlines(keepends=True)
    for n in range(len(lines)):
        yield "".join(lines[:n])
        yield "".join(lines[:n] + lines[n + 1 :])
    for n in range(0, len(src), 5):
        yield src[:n]


def robustness(cases: list[Path]) -> list[str]:
    failures = []
    for case in cases:
        src = case.read_text()
        for variant in [src, *mutations(src)]:
            run = helix("run", "-", stdin=variant)
            if (why := malformed_state(run.stdout, run.returncode)) is not None:
                failures.append(f"{case.name}: {why}\n--- program\n{variant}--- stderr\n{run.stderr}")
                break
    return failures


def test(bless: bool, suites: list[str] | None = None) -> int:
    if (code := build()) != 0:
        return code
    cases = [c for c in sorted(CASES.glob("*.yaml")) if not c.name.endswith(".expected.yaml")]
    modules = helix("std")
    if modules.returncode != 0 or modules.stdout.splitlines() != ["std:math", "std:list", "std:group"]:
        failures.append("standard library discovery failed")
    for source in sorted((ROOT / "std").glob("*.yaml")):
        shown = helix("std", source.stem)
        if shown.returncode != 0 or shown.stdout != source.read_text():
            failures.append(f"{source.name}: standard library source differs")
    if helix("std", "unknown").returncode != 2:
        failures.append("unknown standard library must exit with usage error")
    for case in cases if "golden" in selected else []:
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
    if not bless and "robustness" in selected:
        failures += robustness(cases)
    for f in failures:
        print("FAIL", f, file=sys.stderr)
    print(f"{len(cases) - len(failures)}/{len(cases)} cases passed")
    return 1 if failures else 0


SUITES = ("golden", "robustness", "harness", "cli", "generated", "imports", "library")


def main() -> int:
    if sys.argv[1:2] == ["lint"]:  # forward everything verbatim, including flags argparse would reject
        return lint(sys.argv[2:])
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    sub.add_parser("run").add_argument("file")
    testing = sub.add_parser("test")
    testing.add_argument("--bless", action="store_true")
    testing.add_argument("--suite", action="append", choices=SUITES, help="repeat to select suites; default: all")
    testing.add_argument("--list", action="store_true", help="list available suites")
    sub.add_parser("std").add_argument("module", nargs="?")
    sub.add_parser("lint", help="forwards all remaining args to hyperlinter")
    args = ap.parse_args()
    if args.cmd == "build":
        return build()
    if args.cmd == "run":
        return build() or subprocess.call([str(BIN), "run", args.file], cwd=ROOT)
    if args.cmd == "std":
        return build() or subprocess.call([str(BIN), "std", *([args.module] if args.module else [])], cwd=ROOT)
    if args.list:
        print("\n".join(SUITES))
        return 0
    if args.bless and args.suite and args.suite != ["golden"]:
        ap.error("--bless requires --suite golden (or no suite selection)")
    return test(args.bless, args.suite)


if __name__ == "__main__":
    sys.exit(main())
