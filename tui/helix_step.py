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
from typing import Callable

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


WRAPPER_VM_NAME = "__debug_target__"
DOWN_ARROW = "\x1b[B"
UP_ARROW = "\x1b[A"
RIGHT_ARROW = "\x1b[C"
LEFT_ARROW = "\x1b[D"
SPACE_KEY = " "
CONTINUE_OPERATION = "__continue__"
STEP_FORWARD_OPERATION = "__step_forward__"
STEP_BACKWARD_OPERATION = "__step_backward__"
NEXT_BRANCH_OPERATION = "__next_branch__"
PREVIOUS_BRANCH_OPERATION = "__previous_branch__"
FORK_BRANCH_OPERATION = "__fork_branch__"
DEBUG_LOG_FORMAT = (
    "%C(bold blue)%h%C(reset) - %C(bold green)(%ar)%C(reset) "
    "%C(white)%s%C(reset) %C(dim white)- %an%C(reset)%C(auto)%d%C(reset)"
)
YAML_LOADER = YAML(typ="safe")
YAML_DUMPER = YAML()
YAML_DUMPER.default_flow_style = False
YAML_DUMPER.sort_base_mapping_type_on_output = False
YAML_DUMPER.width = 100
YAML_DUMPER.indent(mapping=2, sequence=4, offset=2)
PYGIT2 = None
CLI_OPERATION_FLAGS = (
    ("--continue", CONTINUE_OPERATION, "Run the forward-start action once."),
    ("--step-forward", STEP_FORWARD_OPERATION, "Run the forward-step action once."),
    ("--step-backward", STEP_BACKWARD_OPERATION, "Run the backward-step action once."),
    ("--next-branch", NEXT_BRANCH_OPERATION, "Switch to the next local branch once."),
    ("--previous-branch", PREVIOUS_BRANCH_OPERATION, "Switch to the previous local branch once."),
    ("--fork-branch", FORK_BRANCH_OPERATION, "Create a new branch at the current HEAD once."),
)
GIT_LOG_BASE_COMMAND = [
    "git",
    "log",
    "--graph",
    "--abbrev-commit",
    "--all",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Snapshot and navigate Helix VM execution state in a per-target debug git repo."
    )
    parser.add_argument("target", help="Path to the Helix VM YAML file to debug.")
    parser.add_argument(
        "--binary",
        default=None,
        help="Path to the compiled helix binary. Defaults to ../build/helix relative to this script.",
    )
    parser.add_argument(
        "--tui",
        action="store_true",
        help="Enable interactive terminal controls instead of running a single forward step and exiting.",
    )
    for flag, operation, help_text in CLI_OPERATION_FLAGS:
        parser.add_argument(
            flag,
            dest="operations",
            action="append_const",
            const=operation,
            help=help_text,
        )
    parser.set_defaults(operations=[])
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


def load_target_vm(target_path: Path, include_source_path: Path | None = None) -> dict:
    with target_path.open("r", encoding="utf-8") as handle:
        loaded = YAML_LOADER.load(handle)

    if not isinstance(loaded, dict):
        raise ValueError("target YAML must be a top-level mapping representing a Helix VM")

    return expand_root_includes(loaded, include_source_path or target_path)


def build_wrapper_vm(target_vm: dict, forward_primitive: str) -> dict:
    return {
        WRAPPER_VM_NAME: target_vm,
        "main": [forward_primitive, WRAPPER_VM_NAME],
    }


def extract_named_top_level_yaml_block(text: str, field_name: str) -> str:
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
        named_block = extract_named_top_level_yaml_block(completed.stdout, WRAPPER_VM_NAME)
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


def load_pygit2():
    global PYGIT2
    if PYGIT2 is not None:
        return PYGIT2

    try:
        import pygit2 as loaded_pygit2
    except ImportError as error:
        raise RuntimeError("pygit2 is required for debug snapshot storage") from error

    PYGIT2 = loaded_pygit2
    return PYGIT2


def open_debug_repo(debug_directory: Path):
    pygit2 = load_pygit2()
    return pygit2.Repository(str(debug_directory / ".git"))


def init_debug_repo(debug_directory: Path) -> None:
    pygit2 = load_pygit2()
    pygit2.init_repository(str(debug_directory), initial_head="main")


def debug_repo_exists(debug_directory: Path) -> bool:
    try:
        repo = open_debug_repo(debug_directory)
    except Exception:
        return False

    return Path(repo.workdir).resolve() == debug_directory.resolve()


def debug_repo_has_commits(debug_directory: Path) -> bool:
    return not open_debug_repo(debug_directory).head_is_unborn


def commit_debug_snapshot(debug_directory: Path, message: str) -> None:
    pygit2 = load_pygit2()
    repo = open_debug_repo(debug_directory)
    signature = pygit2.Signature("Hx Db", "helix-debugger@example.com")
    repo.index.add_all()
    repo.index.write()
    tree = repo.index.write_tree()
    parents = [] if repo.head_is_unborn else [repo.head.target]
    repo.create_commit("HEAD", signature, signature, message, tree, parents)


def debug_repo_has_uncommitted_changes(debug_directory: Path) -> bool:
    return bool(open_debug_repo(debug_directory).status())


def run_git_log(debug_directory: Path, *args: str) -> str:
    completed = subprocess.run(
        [*GIT_LOG_BASE_COMMAND, *args],
        cwd=debug_directory,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise RuntimeError(stderr or "git log failed")

    return completed.stdout


def debug_log(debug_directory: Path) -> str:
    return run_git_log(
        debug_directory,
        "--decorate",
        "--color=always",
        f"--format=format:{DEBUG_LOG_FORMAT}",
    )


class DebugLogRenderer:
    def __init__(self) -> None:
        self.rendered_lines = 0

    def refresh(self, debug_directory: Path) -> None:
        self.clear()

        log_output = debug_log(debug_directory).rstrip("\n")
        if log_output:
            sys.stdout.write(log_output)
            sys.stdout.write("\n")
            self.rendered_lines = len(log_output.splitlines())
        else:
            self.rendered_lines = 0

        sys.stdout.flush()

    def clear(self) -> None:
        if not self.rendered_lines:
            return

        sys.stdout.write(f"\x1b[{self.rendered_lines}F")
        sys.stdout.write("\x1b[J")


def ensure_debug_repo(target_path: Path) -> Path:
    debug_directory = debug_directory_for(target_path)
    debug_target_path = debug_target_path_for(target_path)
    debug_directory.mkdir(exist_ok=True)

    if not debug_repo_exists(debug_directory):
        init_debug_repo(debug_directory)

    if not debug_target_path.exists():
        shutil.copy2(target_path, debug_target_path)

    if not debug_repo_has_commits(debug_directory):
        commit_debug_snapshot(debug_directory, "Record initial debug VM state")

    return debug_target_path


def step_target_file(
    binary_path: Path,
    target_path: Path,
    include_source_path: Path,
    forward_primitive: str,
) -> None:
    if not binary_path.is_file():
        raise FileNotFoundError(f"helix binary not found: {binary_path}")

    target_vm = load_target_vm(target_path, include_source_path)
    sequence, current_index = next_step_context(target_vm)
    if current_index < 0 or current_index >= len(sequence):
        raise ValueError("target VM has no remaining step to execute")

    execution_vm = single_step_execution_vm(target_vm, sequence[current_index])
    wrapper_vm = build_wrapper_vm(execution_vm, forward_primitive)

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        suffix=".yaml",
        prefix=".helix-step-",
        dir=include_source_path.parent,
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


def checkout_detached_commit(repo, commit) -> None:
    repo.checkout_tree(commit)
    repo.set_head(commit.id)


def checkout_branch_ref(repo, reference_name: str) -> None:
    reference = repo.lookup_reference(reference_name)
    repo.checkout_tree(repo[reference.target])
    repo.set_head(reference_name)


def next_fork_branch_ref(repo) -> str:
    existing_branches = local_branch_reference_set(repo)
    branch_index = 1

    while f"refs/heads/fork-{branch_index}" in existing_branches:
        branch_index += 1

    return f"refs/heads/fork-{branch_index}"


def create_branch_at_head(debug_target_path: Path) -> None:
    repo = open_debug_repo(debug_target_path.parent)
    reference_name = next_fork_branch_ref(repo)
    repo.create_reference(reference_name, repo.head.target)
    checkout_branch_ref(repo, reference_name)


def commit_manual_edit_to_fork(debug_target_path: Path) -> None:
    if not debug_repo_has_uncommitted_changes(debug_target_path.parent):
        return

    create_branch_at_head(debug_target_path)
    commit_debug_snapshot(debug_target_path.parent, "Manual VM edit")


def branch_log_order(debug_directory: Path) -> list[str]:
    git_log = run_git_log(
        debug_directory,
        "--decorate=full",
        "--format=format:%x1f%D",
    )

    ordered_branches = []
    seen_branches = set()
    for line in git_log.splitlines():
        _, separator, decorations = line.partition("\x1f")
        if not separator:
            continue

        for decoration in decorations.split(", "):
            if decoration.startswith("HEAD -> "):
                decoration = decoration.removeprefix("HEAD -> ")

            if decoration.startswith("refs/heads/"):
                reference_name = decoration
            else:
                continue

            if reference_name not in seen_branches:
                ordered_branches.append(reference_name)
                seen_branches.add(reference_name)

    return ordered_branches


def local_branch_reference_set(repo) -> set[str]:
    return {
        reference_name
        for reference_name in repo.listall_references()
        if reference_name.startswith("refs/heads/")
    }


def ordered_local_branch_references(repo, ordered_branches: list[str]) -> list[str]:
    local_branches = local_branch_reference_set(repo)
    ordered_branch_set = set(ordered_branches)
    return [
        *[reference_name for reference_name in ordered_branches if reference_name in local_branches],
        *sorted(local_branches - ordered_branch_set),
    ]


def current_branch_index(repo, branch_references: list[str]) -> int:
    if not repo.head_is_detached:
        head_name = repo.head.name
        if head_name in branch_references:
            return branch_references.index(head_name)

    head_target = repo.head.target
    for index, reference_name in enumerate(branch_references):
        if repo.lookup_reference(reference_name).target == head_target:
            return index

    return 0


def checkout_adjacent_branch(debug_target_path: Path, offset: int) -> None:
    repo = open_debug_repo(debug_target_path.parent)
    branch_references = ordered_local_branch_references(
        repo,
        branch_log_order(debug_target_path.parent),
    )
    if not branch_references:
        raise RuntimeError("debug repository has no local branches")

    current_index = current_branch_index(repo, branch_references)
    next_index = (current_index + offset) % len(branch_references)
    checkout_branch_ref(repo, branch_references[next_index])


def next_preserved_snapshot(repo):
    head_id = repo.head.target
    for reference_name in repo.listall_references():
        if not reference_name.startswith("refs/helix-debug/snapshots/"):
            continue

        commit = repo[repo.lookup_reference(reference_name).target]
        if commit.parents and commit.parents[0].id == head_id:
            return commit

    return None


def step_and_commit(
    binary_path: Path,
    debug_target_path: Path,
    include_source_path: Path,
    forward_primitive: str,
) -> None:
    repo = open_debug_repo(debug_target_path.parent)
    if repo.head_is_detached:
        next_snapshot = next_preserved_snapshot(repo)
        if next_snapshot is not None:
            checkout_detached_commit(repo, next_snapshot)
            return

    step_target_file(binary_path, debug_target_path, include_source_path, forward_primitive)
    commit_debug_snapshot(debug_target_path.parent, "VM state")


def preserve_snapshot_ref(repo, commit_id) -> None:
    repo.create_reference(
        f"refs/helix-debug/snapshots/{str(commit_id)[:12]}",
        commit_id,
        force=True,
    )


def checkout_previous_snapshot(debug_target_path: Path) -> None:
    repo = open_debug_repo(debug_target_path.parent)
    head_commit = repo[repo.head.target]
    if not head_commit.parents:
        return

    parent_commit = head_commit.parents[0]
    preserve_snapshot_ref(repo, head_commit.id)
    checkout_detached_commit(repo, parent_commit)


def operation_handlers(
    binary_path: Path,
    debug_target_path: Path,
    include_source_path: Path,
) -> dict[str, Callable[[], None]]:
    return {
        STEP_FORWARD_OPERATION: lambda: step_and_commit(
            binary_path,
            debug_target_path,
            include_source_path,
            "step",
        ),
        UP_ARROW: lambda: step_and_commit(
            binary_path,
            debug_target_path,
            include_source_path,
            "step",
        ),
        CONTINUE_OPERATION: lambda: step_and_commit(
            binary_path,
            debug_target_path,
            include_source_path,
            "start",
        ),
        STEP_BACKWARD_OPERATION: lambda: checkout_previous_snapshot(debug_target_path),
        DOWN_ARROW: lambda: checkout_previous_snapshot(debug_target_path),
        NEXT_BRANCH_OPERATION: lambda: checkout_adjacent_branch(debug_target_path, 1),
        RIGHT_ARROW: lambda: checkout_adjacent_branch(debug_target_path, 1),
        PREVIOUS_BRANCH_OPERATION: lambda: checkout_adjacent_branch(debug_target_path, -1),
        LEFT_ARROW: lambda: checkout_adjacent_branch(debug_target_path, -1),
        FORK_BRANCH_OPERATION: lambda: create_branch_at_head(debug_target_path),
        SPACE_KEY: lambda: create_branch_at_head(debug_target_path),
    }


def execute_debug_operation(
    operation: str,
    binary_path: Path,
    debug_target_path: Path,
    include_source_path: Path,
) -> bool:
    handler = operation_handlers(binary_path, debug_target_path, include_source_path).get(operation)
    if handler is None:
        return False

    if debug_repo_has_uncommitted_changes(debug_target_path.parent):
        commit_manual_edit_to_fork(debug_target_path)
        if operation in (FORK_BRANCH_OPERATION, SPACE_KEY):
            return True

    handler()
    return True


def main() -> int:
    args = parse_args()
    target_path = Path(args.target).expanduser().resolve()
    binary_path = resolve_binary_path(args.binary)

    try:
        debug_target_path = ensure_debug_repo(target_path)
        if not args.tui:
            operations = args.operations or [STEP_FORWARD_OPERATION]
            for operation in operations:
                execute_debug_operation(
                    operation,
                    binary_path,
                    debug_target_path,
                    target_path,
                )
            return 0

        log_renderer = DebugLogRenderer()
        log_renderer.refresh(debug_target_path.parent)
        while True:
            key = read_debug_key()
            if execute_debug_operation(
                key,
                binary_path,
                debug_target_path,
                target_path,
            ):
                log_renderer.refresh(debug_target_path.parent)
            else:
                return 0
    except KeyboardInterrupt:
        return 0
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
