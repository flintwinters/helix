#!/usr/bin/env python3

import argparse
from copy import deepcopy
import shutil
import subprocess
import sys
import tempfile
import termios
import tty
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


WRAPPER_VM_NAME = "__debug_target__"
DOWN_ARROW = "\x1b[B"
UP_ARROW = "\x1b[A"
YAML_LOADER = YAML(typ="safe")
YAML_DUMPER = YAML()
YAML_DUMPER.default_flow_style = False
YAML_DUMPER.sort_base_mapping_type_on_output = False
YAML_DUMPER.width = 100
YAML_DUMPER.indent(mapping=2, sequence=4, offset=2)


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
        loaded = YAML_LOADER.load(handle)

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
        loaded = YAML_LOADER.load(named_block)
    except Exception as error:
        raise RuntimeError("helix did not emit a valid stepped VM block") from error

    if not isinstance(loaded, dict):
        raise RuntimeError("helix output must be a top-level mapping")

    return loaded


def extract_stepped_vm(wrapper_output: dict) -> dict:
    stepped_vm = wrapper_output.get(WRAPPER_VM_NAME)
    if not isinstance(stepped_vm, dict):
        raise RuntimeError("helix output did not contain the stepped child VM")

    return stepped_vm


def list_frame_from_state(target_vm: dict):
    state = target_vm.get("state")
    if not isinstance(state, dict):
        return None

    frames = state.get("frames")
    if not isinstance(frames, list) or not frames:
        return None

    frame = frames[0]
    if not isinstance(frame, dict):
        raise ValueError("state.frames[0] must be a mapping")

    if frame.get("name") != "list":
        raise ValueError("only list-backed VM frames are supported")

    values = frame.get("values")
    index = frame.get("index")
    if not isinstance(values, list) or not isinstance(index, int):
        raise ValueError("list frame must contain list values and an integer index")

    return frame


def sequence_from_main(target_vm: dict) -> list:
    main = target_vm.get("main")
    if not isinstance(main, list) or len(main) != 2 or main[0] != "list":
        raise ValueError("target VM must use main: [list, <sequence-name>] for stepped execution")

    sequence_name = main[1]
    if not isinstance(sequence_name, str):
        raise ValueError("list-backed main must reference a named sequence")

    sequence = target_vm.get(sequence_name)
    if not isinstance(sequence, list):
        raise ValueError(f"target VM sequence {sequence_name!r} must be a YAML list")

    return sequence


def next_step_context(target_vm: dict) -> tuple[list, int]:
    frame = list_frame_from_state(target_vm)
    if frame is not None:
        return frame["values"], frame["index"]

    return sequence_from_main(target_vm), 0


def single_step_execution_vm(target_vm: dict, step_expression) -> dict:
    execution_vm = deepcopy(target_vm)
    execution_vm["main"] = deepcopy(step_expression)
    execution_vm.pop("state", None)
    return execution_vm


def stepped_state(sequence: list, next_index: int) -> dict:
    if next_index < len(sequence):
        return {
            "status": "running",
            "frames": [
                {
                    "name": "list",
                    "index": next_index,
                    "values": deepcopy(sequence),
                }
            ],
        }

    return {
        "status": "finished",
        "frames": [],
        "result": None,
    }


def merge_single_step_result(original_vm: dict, stepped_execution_vm: dict, sequence: list, current_index: int) -> dict:
    merged_vm = deepcopy(original_vm)

    for key, value in stepped_execution_vm.items():
        if key == "main":
            continue
        merged_vm[key] = value

    merged_vm["main"] = deepcopy(original_vm["main"])
    merged_vm["state"] = stepped_state(sequence, current_index + 1)
    return merged_vm


def reorder_like_template(current, template):
    if isinstance(current, dict):
        template_dict = template if isinstance(template, dict) else {}
        ordered = {}

        for key, template_value in template_dict.items():
            if key in current:
                ordered[key] = reorder_like_template(current[key], template_value)

        for key in sorted(current.keys() - template_dict.keys()):
            ordered[key] = reorder_like_template(current[key], None)

        return ordered

    if isinstance(current, list):
        template_list = template if isinstance(template, list) else []
        return [
            reorder_like_template(
                value,
                template_list[index] if index < len(template_list) else None,
            )
            for index, value in enumerate(current)
        ]

    return current


def should_use_flow_style(sequence) -> bool:
    if not sequence:
        return True

    if all(not isinstance(value, (dict, list)) for value in sequence):
        return True

    return (
        len(sequence) <= 4
        and isinstance(sequence[0], str)
        and all(not isinstance(value, dict) for value in sequence[1:])
    )


def to_ruamel_node(data):
    if isinstance(data, dict):
        node = CommentedMap()
        for key, value in data.items():
            node[key] = to_ruamel_node(value)
        return node

    if isinstance(data, list):
        node = CommentedSeq()
        for value in data:
            node.append(to_ruamel_node(value))
        if should_use_flow_style(data):
            node.fa.set_flow_style()
        return node

    return data


def dump_yaml(data: dict, destination: Path) -> None:
    with destination.open("w", encoding="utf-8") as handle:
        YAML_DUMPER.dump(to_ruamel_node(data), handle)


def debug_directory_for(target_path: Path) -> Path:
    return target_path.parent / f"debug_{target_path.stem}"


def debug_target_path_for(target_path: Path) -> Path:
    return debug_directory_for(target_path) / target_path.name


def run_git(debug_directory: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", *args],
        cwd=debug_directory,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise RuntimeError(stderr or f"git {' '.join(args)} failed")


def commit_debug_snapshot(debug_directory: Path, message: str) -> None:
    run_git(debug_directory, "add", ".")
    run_git(
        debug_directory,
        "-c",
        "user.name=Helix Debugger",
        "-c",
        "user.email=helix-debugger@example.invalid",
        "commit",
        "-m",
        message,
    )


def ensure_debug_repo(target_path: Path) -> Path:
    debug_directory = debug_directory_for(target_path)
    debug_target_path = debug_target_path_for(target_path)
    debug_directory.mkdir(exist_ok=True)

    if not (debug_directory / ".git").is_dir():
        run_git(debug_directory, "init")

    if not debug_target_path.exists():
        shutil.copy2(target_path, debug_target_path)
        commit_debug_snapshot(debug_directory, "Record initial debug VM state")

    return debug_target_path


def step_target_file(binary_path: Path, target_path: Path) -> None:
    if not binary_path.is_file():
        raise FileNotFoundError(f"helix binary not found: {binary_path}")

    target_vm = load_target_vm(target_path)
    sequence, current_index = next_step_context(target_vm)
    if current_index < 0 or current_index >= len(sequence):
        raise ValueError("target VM has no remaining step to execute")

    execution_vm = single_step_execution_vm(target_vm, sequence[current_index])
    wrapper_vm = build_wrapper_vm(execution_vm)

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        suffix=".yaml",
        prefix=".helix-step-",
        dir=target_path.parent,
        delete=False,
    ) as handle:
        wrapper_path = Path(handle.name)
        YAML_DUMPER.dump(to_ruamel_node(wrapper_vm), handle)

    try:
        wrapper_output = run_helix(binary_path, wrapper_path)
        stepped_execution_vm = extract_stepped_vm(wrapper_output)
        stepped_vm = merge_single_step_result(
            target_vm,
            stepped_execution_vm,
            sequence,
            current_index,
        )
        ordered_vm = reorder_like_template(stepped_vm, target_vm)
        dump_yaml(ordered_vm, target_path)
    finally:
        wrapper_path.unlink(missing_ok=True)


def read_debug_key() -> str:
    stdin_fd = sys.stdin.fileno()
    original_settings = termios.tcgetattr(stdin_fd)
    try:
        tty.setraw(stdin_fd)
        key = sys.stdin.read(1)
        if key == "\x1b":
            key += sys.stdin.read(2)
    finally:
        termios.tcsetattr(stdin_fd, termios.TCSADRAIN, original_settings)

    return key


def step_and_commit(binary_path: Path, debug_target_path: Path) -> None:
    step_target_file(binary_path, debug_target_path)
    commit_debug_snapshot(debug_target_path.parent, "Record stepped debug VM state")


def reset_previous_snapshot(debug_target_path: Path) -> None:
    run_git(debug_target_path.parent, "reset", "--hard", "HEAD~1")


def main() -> int:
    args = parse_args()
    target_path = Path(args.target).expanduser().resolve()
    binary_path = resolve_binary_path(args.binary)

    try:
        debug_target_path = ensure_debug_repo(target_path)
        while True:
            key = read_debug_key()
            if key == DOWN_ARROW:
                step_and_commit(binary_path, debug_target_path)
            elif key == UP_ARROW:
                reset_previous_snapshot(debug_target_path)
            else:
                return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
