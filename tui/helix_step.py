#!/usr/bin/env python3

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


WRAPPER_VM_NAME = "__debug_target__"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Advance a Helix VM YAML file by one step using the compiled helix binary."
    )
    parser.add_argument("target", help="Path to the Helix VM YAML file to step in place.")
    parser.add_argument(
        "--binary",
        default=None,
        help="Path to the compiled helix binary. Defaults to ../build/helix relative to this script.",
    )
    return parser.parse_args()


def resolve_binary_path(raw_binary_path: str | None) -> Path:
    if raw_binary_path is not None:
        return Path(raw_binary_path).expanduser().resolve()

    return (Path(__file__).resolve().parents[1] / "build" / "helix").resolve()


def resolve_include_path(source_path: Path, include_name: str) -> Path:
    include_path = Path(include_name)
    if include_path.is_absolute():
        return include_path

    return source_path.parent / include_path


def include_binding_name(include_path: Path) -> str:
    return include_path.stem


def expand_root_includes(target_vm: dict, source_path: Path) -> dict:
    include_entries = target_vm.get("include")
    if include_entries is None:
        return target_vm

    if not isinstance(include_entries, list) or not all(isinstance(entry, str) for entry in include_entries):
        raise ValueError("include must be a YAML sequence of string paths")

    expanded_vm = dict(target_vm)
    for include_name in include_entries:
        include_path = resolve_include_path(source_path, include_name)
        included_vm = load_target_vm(include_path)
        expanded_vm[include_binding_name(include_path)] = included_vm

    return expanded_vm


def load_target_vm(target_path: Path) -> dict:
    with target_path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)

    if not isinstance(loaded, dict):
        raise ValueError("target YAML must be a top-level mapping representing a Helix VM")

    return expand_root_includes(loaded, target_path)


def build_wrapper_vm(target_vm: dict) -> dict:
    return {
        WRAPPER_VM_NAME: target_vm,
        "main": ["step", WRAPPER_VM_NAME],
    }


def extract_named_top_level_block(text: str, field_name: str) -> str:
    lines = text.splitlines()
    marker = f"{field_name}:"
    start_index = None

    for index, line in enumerate(lines):
        if line == marker:
            start_index = index

    if start_index is None:
        raise RuntimeError(f"helix output did not contain a top-level {field_name!r} block")

    block_lines = [lines[start_index]]
    for line in lines[start_index + 1 :]:
        if line and not line[:1].isspace():
            break
        block_lines.append(line)

    return "\n".join(block_lines) + "\n"


def run_helix(binary_path: Path, wrapper_path: Path) -> dict:
    completed = subprocess.run(
        [str(binary_path), str(wrapper_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise RuntimeError(stderr or "helix exited with a non-zero status")

    try:
        named_block = extract_named_top_level_block(completed.stdout, WRAPPER_VM_NAME)
        loaded = yaml.safe_load(named_block)
    except yaml.YAMLError as error:
        raise RuntimeError("helix did not emit a valid stepped VM block") from error

    if not isinstance(loaded, dict):
        raise RuntimeError("helix output must be a top-level mapping")

    return loaded


def extract_stepped_vm(wrapper_output: dict) -> dict:
    stepped_vm = wrapper_output.get(WRAPPER_VM_NAME)
    if not isinstance(stepped_vm, dict):
        raise RuntimeError("helix output did not contain the stepped child VM")

    return stepped_vm


def dump_yaml(data: dict, destination: Path) -> None:
    with destination.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(
            data,
            handle,
            sort_keys=False,
            default_flow_style=False,
        )


def step_target_file(binary_path: Path, target_path: Path) -> None:
    if not binary_path.is_file():
        raise FileNotFoundError(f"helix binary not found: {binary_path}")

    target_vm = load_target_vm(target_path)
    wrapper_vm = build_wrapper_vm(target_vm)

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        suffix=".yaml",
        prefix=".helix-step-",
        dir=target_path.parent,
        delete=False,
    ) as handle:
        wrapper_path = Path(handle.name)
        yaml.safe_dump(wrapper_vm, handle, sort_keys=False, default_flow_style=False)

    try:
        wrapper_output = run_helix(binary_path, wrapper_path)
        stepped_vm = extract_stepped_vm(wrapper_output)
        dump_yaml(stepped_vm, target_path)
    finally:
        wrapper_path.unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    target_path = Path(args.target).expanduser().resolve()
    binary_path = resolve_binary_path(args.binary)

    try:
        step_target_file(binary_path, target_path)
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
