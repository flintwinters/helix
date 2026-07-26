#!/usr/bin/env python3
"""Deterministic Python prototype for literal-YAML HUI interaction."""

from __future__ import annotations

import argparse
import io
import os
import shutil
import sys
import termios
import tty
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable, Iterable, Protocol

from ruamel.yaml import YAML

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tui.helix_step import load_target_vm, resolve_binary_path, step_target_file


ESCAPE = "\x1b"
UP = "\x1b[A"
DOWN = "\x1b[B"
RIGHT = "\x1b[C"
LEFT = "\x1b[D"
PAGE_UP = "\x1b[5~"
PAGE_DOWN = "\x1b[6~"
F5 = "\x1b[15~"
F10 = "\x1b[21~"
CTRL_Q = "\x11"

RESET = "\x1b[0m"
BG = "\x1b[48;5;235m"
CHROME = "\x1b[38;5;235;48;5;214m"
PATH_STYLE = "\x1b[38;5;109;48;5;237m"
FOOTER_STYLE = "\x1b[38;5;223;48;5;237m"
YAML_STYLE = "\x1b[38;5;223m"
PC_STYLE = "\x1b[38;5;235;48;5;167m"
SELECTION_STYLE = "\x1b[38;5;235;48;5;142m"
PC_SELECTION_STYLE = "\x1b[38;5;235;48;5;208m"
CLEAR_HOME = "\x1b[2J\x1b[H"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"

MAIN_FIELD = "main"
STATE_FIELD = "state"
STATUS_FIELD = "status"
FRAMES_FIELD = "frames"
KEYBINDS_FIELD = "keybinds"
RUNNING_STATUS = "running"

PathTuple = tuple[str | int, ...]

YAML_ROUND_TRIP = YAML(typ="rt")
YAML_ROUND_TRIP.preserve_quotes = True
YAML_DUMPER = YAML()
YAML_DUMPER.default_flow_style = False
YAML_DUMPER.sort_base_mapping_type_on_output = False
YAML_DUMPER.width = 100
YAML_DUMPER.indent(mapping=2, sequence=4, offset=2)


@dataclass(frozen=True)
class SemanticNode:
    path: PathTuple
    parent: PathTuple | None
    line: int


@dataclass(frozen=True)
class DemoState:
    selection: PathTuple
    viewport: int = 0
    context_vm: PathTuple | None = None
    exiting: bool = False


@dataclass(frozen=True)
class Document:
    data: dict
    text: str
    lines: tuple[str, ...]
    nodes: tuple[SemanticNode, ...]
    node_by_path: dict[PathTuple, SemanticNode]
    active_vm: PathTuple


class Terminal(Protocol):
    def __enter__(self) -> "Terminal": ...
    def __exit__(self, exc_type, exc, traceback) -> None: ...
    def read_key(self) -> str: ...
    def write(self, text: str) -> None: ...
    def dimensions(self) -> tuple[int, int]: ...


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prototype literal-YAML Helix terminal interface.")
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Ordered nested-VM YAML document to display.",
    )
    parser.add_argument("--binary", default=None, help="Path to the compiled Helix runtime.")
    return parser.parse_args(arguments)


def prepare_target_path(raw_target: str | None) -> Path:
    if raw_target is not None:
        return Path(raw_target).expanduser().resolve()

    source_path = Path(__file__).with_name("hui_demo.yaml")
    target_path = Path(__file__).resolve().parents[1] / "build" / "hui_demo.yaml"
    target_path.parent.mkdir(exist_ok=True)
    shutil.copy2(source_path, target_path)
    return target_path


def canonical_yaml(data: dict) -> str:
    stream = io.StringIO()
    YAML_DUMPER.dump(data, stream)
    return stream.getvalue()


def load_ordered_document(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        loaded = YAML_ROUND_TRIP.load(handle)
    if not isinstance(loaded, dict):
        raise ValueError("HUI document must be a top-level YAML mapping")
    return loaded


def value_at(data, path: PathTuple):
    node = data
    for segment in path:
        if isinstance(node, dict) and segment in node:
            node = node[segment]
        elif isinstance(node, list) and isinstance(segment, int) and 0 <= segment < len(node):
            node = node[segment]
        else:
            return None
    return node


def is_vm(node) -> bool:
    return isinstance(node, dict) and MAIN_FIELD in node


def vm_paths(data) -> list[PathTuple]:
    paths: list[PathTuple] = []

    def visit(node, path: PathTuple) -> None:
        if is_vm(node):
            paths.append(path)
        if isinstance(node, dict):
            for key, child in node.items():
                visit(child, (*path, key))
        elif isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, (*path, index))

    visit(data, ())
    return paths


def is_path_prefix(prefix: PathTuple, path: PathTuple) -> bool:
    return path[: len(prefix)] == prefix


def discover_active_vm(data) -> PathTuple:
    running = []
    for path in vm_paths(data):
        vm = value_at(data, path)
        state = vm.get(STATE_FIELD)
        if isinstance(state, dict) and state.get(STATUS_FIELD) == RUNNING_STATUS:
            running.append(path)

    if not running:
        return ()

    deepest = max(running, key=len)
    if not all(is_path_prefix(path, deepest) for path in running):
        raise ValueError("demo requires one unambiguous running VM ancestry chain")
    return deepest


def pc_path(data, vm_path: PathTuple) -> PathTuple:
    vm = value_at(data, vm_path)
    if not is_vm(vm):
        raise ValueError(f"active execution context is not a VM: {format_path(vm_path)}")
    state = vm.get(STATE_FIELD)
    frames = state.get(FRAMES_FIELD) if isinstance(state, dict) else None
    if isinstance(frames, list) and frames and isinstance(frames[0], list):
        return (*vm_path, *frames[0])
    return (*vm_path, MAIN_FIELD)


def semantic_nodes(parsed, data) -> tuple[SemanticNode, ...]:
    nodes: list[SemanticNode] = [SemanticNode((), None, 0)]

    def visit(node, path: PathTuple) -> None:
        if isinstance(node, dict):
            for key, child in node.items():
                child_path = (*path, key)
                line, _ = node.lc.value(key)
                nodes.append(SemanticNode(child_path, path, line))
                visit(child, child_path)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                child_path = (*path, index)
                line, _ = node.lc.item(index)
                nodes.append(SemanticNode(child_path, path, line))
                visit(child, child_path)

    visit(parsed, ())
    return tuple(nodes)


def build_document(data: dict) -> Document:
    text = canonical_yaml(data)
    parsed = YAML_ROUND_TRIP.load(text)
    nodes = semantic_nodes(parsed, data)
    return Document(
        data=data,
        text=text,
        lines=tuple(text.rstrip("\n").splitlines()),
        nodes=nodes,
        node_by_path={node.path: node for node in nodes},
        active_vm=discover_active_vm(data),
    )


def first_child(document: Document, path: PathTuple) -> PathTuple | None:
    for node in document.nodes:
        if node.parent == path:
            return node.path
    return None


def move_selection(document: Document, state: DemoState, offset: int) -> DemoState:
    index = next(
        (index for index, node in enumerate(document.nodes) if node.path == state.selection),
        0,
    )
    next_index = max(0, min(len(document.nodes) - 1, index + offset))
    return replace(state, selection=document.nodes[next_index].path)


def vm_ancestors(document: Document, vm_path: PathTuple) -> Iterable[PathTuple]:
    candidates = [
        path
        for path in vm_paths(document.data)
        if is_path_prefix(path, vm_path)
    ]
    yield from sorted(candidates, key=len, reverse=True)


def normalized_appkeys(value) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        return tuple(key for key in value if isinstance(key, str))
    return ()


def resolve_appkey(document: Document, context_vm: PathTuple, key: str) -> PathTuple | None:
    for vm_path in vm_ancestors(document, context_vm):
        vm = value_at(document.data, vm_path)
        if key in normalized_appkeys(vm.get(KEYBINDS_FIELD)):
            return vm_path
    return None


def format_path(path: PathTuple) -> str:
    return "/" if not path else "/" + "/".join(str(segment) for segment in path)


def clamp_viewport(document: Document, state: DemoState, body_height: int) -> DemoState:
    maximum = max(0, len(document.lines) - max(1, body_height))
    viewport = max(0, min(state.viewport, maximum))
    selected = document.node_by_path.get(state.selection)
    if selected is not None:
        if selected.line < viewport:
            viewport = selected.line
        elif selected.line >= viewport + body_height:
            viewport = selected.line - body_height + 1
    return replace(state, viewport=max(0, min(viewport, maximum)))


def reduce_state(
    document: Document,
    state: DemoState,
    key: str,
    body_height: int,
) -> tuple[DemoState, str | None]:
    if key in (CTRL_Q, ESCAPE):
        return replace(state, exiting=True), None
    if key == UP:
        return clamp_viewport(document, move_selection(document, state, -1), body_height), None
    if key == DOWN:
        return clamp_viewport(document, move_selection(document, state, 1), body_height), None
    if key == LEFT:
        node = document.node_by_path.get(state.selection)
        if node is not None and node.parent is not None:
            state = replace(state, selection=node.parent)
        return clamp_viewport(document, state, body_height), None
    if key == RIGHT:
        child = first_child(document, state.selection)
        if child is not None:
            state = replace(state, selection=child)
        return clamp_viewport(document, state, body_height), None
    if key == PAGE_UP:
        return replace(state, viewport=max(0, state.viewport - body_height)), None
    if key == PAGE_DOWN:
        maximum = max(0, len(document.lines) - body_height)
        return replace(state, viewport=min(maximum, state.viewport + body_height)), None
    if key == F10:
        return state, "step"
    if key == F5:
        return state, "start"

    context = state.context_vm if state.context_vm is not None else document.active_vm
    declaring_vm = resolve_appkey(document, context, key)
    if declaring_vm is not None:
        selection = pc_path(document.data, declaring_vm)
        return (
            clamp_viewport(
                document,
                replace(state, context_vm=declaring_vm, selection=selection),
                body_height,
            ),
            None,
        )
    return state, None


def overlay_line(line: str, selected: bool, active_pc: bool) -> str:
    if selected and active_pc:
        style = PC_SELECTION_STYLE
    elif active_pc:
        style = PC_STYLE
    elif selected:
        style = SELECTION_STYLE
    else:
        style = YAML_STYLE
    return f"{style}{line}{RESET}{BG}"


def fit(text: str, width: int) -> str:
    return text[:width].ljust(width)


def render(document: Document, state: DemoState, rows: int, columns: int) -> str:
    rows = max(3, rows)
    columns = max(1, columns)
    content_width = max(1, columns - 1)
    body_height = rows - 2
    maximum_viewport = max(0, len(document.lines) - body_height)
    viewport = max(0, min(state.viewport, maximum_viewport))
    context_vm = state.context_vm if state.context_vm is not None else document.active_vm
    active_pc = pc_path(document.data, context_vm)
    selected_line = document.node_by_path.get(state.selection, document.nodes[0]).line
    pc_node = document.node_by_path.get(active_pc)
    pc_line = pc_node.line if pc_node is not None else -1

    status = (
        f" HUI DEMO  vm:{format_path(context_vm)}  pc:{format_path(active_pc)} "
        f" lines:{viewport + 1}-{min(len(document.lines), viewport + body_height)}"
    )
    output = [
        f"{CLEAR_HOME}{HIDE_CURSOR}{BG}{CHROME}{fit(status, content_width)}{RESET}{BG}"
    ]
    for row in range(body_height):
        line_index = viewport + row
        line = document.lines[line_index] if line_index < len(document.lines) else ""
        output.append(
            overlay_line(
                fit(line, content_width),
                selected=line_index == selected_line,
                active_pc=line_index == pc_line,
            )
        )

    footer = (
        f" {format_path(state.selection)}  ↑↓ node ← parent → child "
        "PgUp/PgDn view F10 step F5 continue Ctrl-Q quit"
    )
    output.append(f"{PATH_STYLE}{fit(footer, content_width)}{RESET}")
    return "\r\n".join(output)


def execute_runtime_operation(
    operation: str,
    binary_path: Path,
    target_path: Path,
) -> dict:
    step_target_file(
        binary_path,
        target_path,
        target_path,
        operation,
        annotate_pc=False,
    )
    return load_target_vm(target_path)


def run_demo(
    target_path: Path,
    binary_path: Path,
    terminal: Terminal,
    runtime_operation: Callable[[str, Path, Path], dict] = execute_runtime_operation,
) -> None:
    document = build_document(load_ordered_document(target_path))
    state = DemoState(selection=pc_path(document.data, document.active_vm))

    with terminal:
        while not state.exiting:
            rows, columns = terminal.dimensions()
            terminal.write(render(document, state, rows, columns))
            key = terminal.read_key()
            state, operation = reduce_state(document, state, key, max(1, rows - 2))
            if operation is not None:
                data = runtime_operation(operation, binary_path, target_path)
                document = build_document(data)
                context = state.context_vm
                if context not in vm_paths(document.data):
                    context = None
                active_vm = context if context is not None else document.active_vm
                state = replace(
                    state,
                    context_vm=context,
                    selection=pc_path(document.data, active_vm),
                )


class PosixTerminal:
    def __init__(self, stdin=None, stdout=None) -> None:
        self.stdin = stdin or sys.stdin
        self.stdout = stdout or sys.stdout
        self._settings = None

    def __enter__(self) -> "PosixTerminal":
        self._settings = termios.tcgetattr(self.stdin.fileno())
        tty.setraw(self.stdin.fileno())
        raw_settings = termios.tcgetattr(self.stdin.fileno())
        raw_settings[6][termios.VMIN] = 0
        raw_settings[6][termios.VTIME] = 1
        termios.tcsetattr(self.stdin.fileno(), termios.TCSANOW, raw_settings)
        self.write(HIDE_CURSOR)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        try:
            if self._settings is not None:
                termios.tcsetattr(self.stdin.fileno(), termios.TCSADRAIN, self._settings)
        finally:
            self.write(f"{RESET}{SHOW_CURSOR}\n")

    def read_key(self) -> str:
        first = ""
        while not first:
            first = self.stdin.read(1)
        if first != ESCAPE:
            return first
        suffix = ""
        while len(suffix) < 5:
            character = self.stdin.read(1)
            if not character:
                break
            suffix += character
            if character.isalpha() or character == "~":
                break
        return first + suffix

    def write(self, text: str) -> None:
        self.stdout.write(text)
        self.stdout.flush()

    def dimensions(self) -> tuple[int, int]:
        size = os.get_terminal_size(self.stdout.fileno())
        return size.lines, size.columns


def main(arguments: list[str] | None = None) -> int:
    args = parse_args(arguments)
    target_path = prepare_target_path(args.target)
    try:
        run_demo(target_path, resolve_binary_path(args.binary), PosixTerminal())
    except Exception as error:
        print(f"hui demo: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
