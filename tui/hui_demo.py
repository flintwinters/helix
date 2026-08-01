#!/usr/bin/env python3
"""Deterministic Python prototype for literal-YAML HUI interaction."""

from __future__ import annotations

import argparse
import io
import os
import shutil
import sys
import termios
import tempfile
import tty
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Callable, Iterable, Protocol

from ruamel.yaml import YAML

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tui.helix_step import (
    CONTINUE_OPERATION,
    STEP_FORWARD_OPERATION,
    YAML_DUMPER,
    ensure_debug_repo,
    execute_debug_operation,
    resolve_binary_path,
    restore_previous_yaml_snapshot,
    running_vm_path,
    vm_can_advance,
)


ESCAPE = "\x1b"
UP = "\x1b[A"
DOWN = "\x1b[B"
RIGHT = "\x1b[C"
LEFT = "\x1b[D"
PAGE_UP = "\x1b[5~"
PAGE_DOWN = "\x1b[6~"
F5 = "\x1b[15~"
F9 = "\x1b[20~"
F10 = "\x1b[21~"
CTRL_Q = "\x11"
BACKSPACE = "\x7f"
DELETE = "\x1b[3~"
HOME = "\x1b[H"
END = "\x1b[F"
ENTER_KEYS = ("\r", "\n")

RESET = "\x1b[0m"
CHROME = "\x1b[38;5;214;1m"
PATH_STYLE = "\x1b[38;5;109m"
YAML_STYLE = "\x1b[38;5;223m"
YAML_KEY_STYLE = "\x1b[38;5;109m"
YAML_STRING_STYLE = "\x1b[38;5;142m"
YAML_NUMBER_STYLE = "\x1b[38;5;208m"
YAML_LITERAL_STYLE = "\x1b[38;5;175m"
YAML_PUNCTUATION_STYLE = "\x1b[38;5;245m"
YAML_COMMENT_STYLE = "\x1b[38;5;243m"
APPKEY_STYLE = "\x1b[3m"
APPKEY_STYLE_END = "\x1b[23m"
PC_STYLE = "\x1b[38;5;167;1m"
SELECTION_STYLE = "\x1b[48;5;237;38;5;142m"
PC_SELECTION_STYLE = "\x1b[48;5;237;38;5;208;1m"
CLEAR_HOME = "\x1b[2J\x1b[H"
HIDE_CURSOR = "\x1b[?25l"
SHOW_CURSOR = "\x1b[?25h"
RUN_MODE = "RUN"
WRITE_MODE = "WRITE"

MAIN_FIELD = "main"
STATE_FIELD = "state"
STATUS_FIELD = "status"
FRAMES_FIELD = "frames"
KEYBINDS_FIELD = "keybinds"
RUNNING_STATUS = "running"

PathTuple = tuple[str | int, ...]

YAML_ROUND_TRIP = YAML(typ="rt")
YAML_ROUND_TRIP.preserve_quotes = True
@dataclass(frozen=True)
class SemanticNode:
    path: PathTuple
    parent: PathTuple | None
    line: int
    column: int = 0


@dataclass(frozen=True)
class EditorBuffer:
    lines: tuple[str, ...]
    cursor_line: int
    cursor_column: int
    trailing_newline: bool = True

    @classmethod
    def from_text(cls, text: str, line: int, column: int) -> "EditorBuffer":
        lines = tuple(text.rstrip("\n").split("\n")) or ("",)
        cursor_line = max(0, min(line, len(lines) - 1))
        cursor_column = max(0, min(column, len(lines[cursor_line])))
        return cls(lines, cursor_line, cursor_column, text.endswith("\n"))

    def text(self) -> str:
        suffix = "\n" if self.trailing_newline else ""
        return "\n".join(self.lines) + suffix


@dataclass(frozen=True)
class DemoState:
    selection: PathTuple
    viewport: int = 0
    context_vm: PathTuple | None = None
    exiting: bool = False
    mode: str = RUN_MODE
    editor: EditorBuffer | None = None
    message: str = ""


@dataclass(frozen=True)
class Document:
    data: dict
    text: str
    lines: tuple[str, ...]
    nodes: tuple[SemanticNode, ...]
    node_by_path: dict[PathTuple, SemanticNode]
    active_vm: PathTuple
    appkey_declarations: tuple["AppkeyDeclaration", ...]


@dataclass(frozen=True)
class AppkeyDeclaration:
    key: str
    vm_path: PathTuple
    line: int
    start: int
    end: int


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

    project_root = Path(__file__).resolve().parents[1]
    source_path = project_root / "tests" / "assets" / "hui_core_demo.yaml"
    build_directory = project_root / "build"
    build_directory.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=build_directory,
        prefix="hui_demo_",
        suffix=".yaml",
        delete=False,
    ) as handle:
        target_path = Path(handle.name)
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


def parse_document(text: str) -> dict:
    loaded = YAML_ROUND_TRIP.load(text)
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
    return running_vm_path(data)


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
                line, column = node.lc.value(key)
                nodes.append(SemanticNode(child_path, path, line, column))
                visit(child, child_path)
        elif isinstance(node, list):
            for index, child in enumerate(node):
                child_path = (*path, index)
                line, column = node.lc.item(index)
                nodes.append(SemanticNode(child_path, path, line, column))
                visit(child, child_path)

    visit(parsed, ())
    return tuple(nodes)


def yaml_scalar_end(line: str, start: int) -> int:
    if start >= len(line):
        return start
    quote = line[start] if line[start] in "\"'" else None
    if quote is not None:
        end = start + 1
        while end < len(line):
            if quote == '"' and line[end] == "\\":
                end += 2
                continue
            if line[end] == quote:
                end += 1
                if quote == "'" and end < len(line) and line[end] == "'":
                    end += 1
                    continue
                return end
            end += 1
        return end

    end = start
    while end < len(line):
        character = line[end]
        if character.isspace() or character in ",]}#":
            break
        end += 1
    return end


def appkey_declarations(
    data,
    node_by_path: dict[PathTuple, SemanticNode],
    lines: tuple[str, ...],
) -> tuple[AppkeyDeclaration, ...]:
    declarations: list[AppkeyDeclaration] = []
    for vm_path in vm_paths(data):
        vm = value_at(data, vm_path)
        keybinds = vm.get(KEYBINDS_FIELD)
        if isinstance(keybinds, str):
            entries = [(None, keybinds)]
        elif isinstance(keybinds, list):
            entries = [
                (index, key)
                for index, key in enumerate(keybinds)
                if isinstance(key, str)
            ]
        else:
            entries = []
        for index, key in entries:
            path = (
                (*vm_path, KEYBINDS_FIELD)
                if index is None
                else (*vm_path, KEYBINDS_FIELD, index)
            )
            node = node_by_path.get(path)
            if node is None:
                continue
            declarations.append(
                AppkeyDeclaration(
                    key,
                    vm_path,
                    node.line,
                    node.column,
                    yaml_scalar_end(lines[node.line], node.column),
                )
            )
    return tuple(declarations)


def build_document(data: dict, text: str | None = None) -> Document:
    text = canonical_yaml(data) if text is None else text
    parsed = YAML_ROUND_TRIP.load(text)
    nodes = semantic_nodes(parsed, data)
    lines = tuple(text.rstrip("\n").splitlines())
    node_by_path = {node.path: node for node in nodes}
    return Document(
        data=data,
        text=text,
        lines=lines,
        nodes=nodes,
        node_by_path=node_by_path,
        active_vm=discover_active_vm(data),
        appkey_declarations=appkey_declarations(data, node_by_path, lines),
    )


def load_document(path: Path) -> Document:
    text = path.read_text(encoding="utf-8")
    return build_document(parse_document(text), text)


def first_child(document: Document, path: PathTuple) -> PathTuple | None:
    for node in document.nodes:
        if node.parent == path:
            return node.path
    return None


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


def active_appkey_spans(
    document: Document,
    context_vm: PathTuple,
) -> tuple[AppkeyDeclaration, ...]:
    """Return exact declarations whose keys dispatch in the current context."""
    active: list[AppkeyDeclaration] = []
    claimed_keys: set[str] = set()
    for vm_path in vm_ancestors(document, context_vm):
        for declaration in document.appkey_declarations:
            if declaration.vm_path != vm_path:
                continue
            if declaration.key in claimed_keys:
                continue
            claimed_keys.add(declaration.key)
            active.append(declaration)
    return tuple(active)


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


def clamp_editor_viewport(state: DemoState, body_height: int) -> DemoState:
    editor = state.editor
    if editor is None:
        return state
    maximum = max(0, len(editor.lines) - max(1, body_height))
    viewport = max(0, min(state.viewport, maximum))
    if editor.cursor_line < viewport:
        viewport = editor.cursor_line
    elif editor.cursor_line >= viewport + body_height:
        viewport = editor.cursor_line - body_height + 1
    return replace(state, viewport=max(0, min(viewport, maximum)))


def replace_editor_line(
    editor: EditorBuffer,
    line: str,
    cursor_line: int | None = None,
    cursor_column: int | None = None,
    lines: tuple[str, ...] | None = None,
) -> EditorBuffer:
    updated_lines = list(editor.lines if lines is None else lines)
    line_index = editor.cursor_line if cursor_line is None else cursor_line
    updated_lines[line_index] = line
    return replace(
        editor,
        lines=tuple(updated_lines),
        cursor_line=line_index,
        cursor_column=(
            editor.cursor_column if cursor_column is None else cursor_column
        ),
    )


def edit_buffer(editor: EditorBuffer, key: str) -> EditorBuffer:
    line_index = editor.cursor_line
    column = editor.cursor_column
    line = editor.lines[line_index]

    if key == UP:
        next_line = max(0, line_index - 1)
        return replace(
            editor,
            cursor_line=next_line,
            cursor_column=min(column, len(editor.lines[next_line])),
        )
    if key == DOWN:
        next_line = min(len(editor.lines) - 1, line_index + 1)
        return replace(
            editor,
            cursor_line=next_line,
            cursor_column=min(column, len(editor.lines[next_line])),
        )
    if key == LEFT:
        if column > 0:
            return replace(editor, cursor_column=column - 1)
        if line_index > 0:
            return replace(
                editor,
                cursor_line=line_index - 1,
                cursor_column=len(editor.lines[line_index - 1]),
            )
        return editor
    if key == RIGHT:
        if column < len(line):
            return replace(editor, cursor_column=column + 1)
        if line_index + 1 < len(editor.lines):
            return replace(editor, cursor_line=line_index + 1, cursor_column=0)
        return editor
    if key == HOME:
        return replace(editor, cursor_column=0)
    if key == END:
        return replace(editor, cursor_column=len(line))
    if key in ENTER_KEYS:
        lines = list(editor.lines)
        lines[line_index : line_index + 1] = [line[:column], line[column:]]
        return replace(
            editor,
            lines=tuple(lines),
            cursor_line=line_index + 1,
            cursor_column=0,
        )
    if key == BACKSPACE:
        if column > 0:
            return replace_editor_line(
                editor,
                line[: column - 1] + line[column:],
                cursor_column=column - 1,
            )
        if line_index > 0:
            lines = list(editor.lines)
            previous = lines[line_index - 1]
            lines[line_index - 1 : line_index + 1] = [previous + line]
            return replace(
                editor,
                lines=tuple(lines),
                cursor_line=line_index - 1,
                cursor_column=len(previous),
            )
        return editor
    if key == DELETE:
        if column < len(line):
            return replace_editor_line(
                editor,
                line[:column] + line[column + 1 :],
            )
        if line_index + 1 < len(editor.lines):
            lines = list(editor.lines)
            lines[line_index : line_index + 2] = [line + lines[line_index + 1]]
            return replace(editor, lines=tuple(lines))
        return editor
    if key == "\t":
        key = "  "
    if key and all(character.isprintable() for character in key):
        return replace_editor_line(
            editor,
            line[:column] + key + line[column:],
            cursor_column=column + len(key),
        )
    return editor


def reduce_state(
    document: Document,
    state: DemoState,
    key: str,
    body_height: int,
) -> tuple[DemoState, str | None]:
    if key == CTRL_Q:
        return replace(state, exiting=True), None
    if state.mode == WRITE_MODE:
        if key == ESCAPE:
            return state, "save"
        editor = state.editor
        if editor is None:
            raise ValueError("write mode requires an editor buffer")
        return (
            clamp_editor_viewport(
                replace(
                    state,
                    editor=edit_buffer(editor, key),
                    message="",
                ),
                body_height,
            ),
            None,
        )
    if key == ESCAPE:
        node = document.node_by_path.get(state.selection, document.nodes[0])
        editor = EditorBuffer.from_text(document.text, node.line, node.column)
        return (
            clamp_editor_viewport(
                replace(
                    state,
                    mode=WRITE_MODE,
                    editor=editor,
                    message="",
                ),
                body_height,
            ),
            None,
        )
    if key == UP:
        return replace(state, context_vm=None), "back"
    if key == DOWN:
        if not vm_can_advance(document.data):
            return state, None
        return replace(state, context_vm=None), "step"
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
        if not vm_can_advance(document.data):
            return state, None
        return replace(state, context_vm=None), "step"
    if key == F5:
        if not vm_can_advance(document.data):
            return state, None
        return replace(state, context_vm=None), "start"
    if key == F9:
        return replace(state, context_vm=None), "back"

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


def yaml_token_style(token: str, is_key: bool) -> str:
    if is_key:
        return YAML_KEY_STYLE
    if token in {"true", "false", "null", "~"}:
        return YAML_LITERAL_STYLE
    try:
        float(token.replace("_", ""))
    except ValueError:
        return YAML_STYLE
    return YAML_NUMBER_STYLE


def highlight_yaml(line: str) -> str:
    """Add foreground colors without changing a single YAML character."""
    output: list[str] = []
    index = 0
    punctuation = "[]{}:,"
    while index < len(line):
        character = line[index]
        if character == "#" and (index == 0 or line[index - 1].isspace()):
            output.append(f"{YAML_COMMENT_STYLE}{line[index:]}")
            break
        if character in "\"'":
            quote = character
            end = index + 1
            while end < len(line):
                if quote == '"' and line[end] == "\\":
                    end += 2
                    continue
                if line[end] == quote:
                    end += 1
                    if quote == "'" and end < len(line) and line[end] == "'":
                        end += 1
                        continue
                    break
                end += 1
            output.append(f"{YAML_STRING_STYLE}{line[index:end]}")
            index = end
            continue
        if character in punctuation or (
            character == "-" and line[:index].strip() == ""
        ):
            output.append(f"{YAML_PUNCTUATION_STYLE}{character}")
            index += 1
            continue
        if character.isspace():
            output.append(character)
            index += 1
            continue

        end = index
        while (
            end < len(line)
            and not line[end].isspace()
            and line[end] not in f"{punctuation}\"'"
        ):
            end += 1
        token = line[index:end]
        remainder = line[end:].lstrip()
        output.append(f"{yaml_token_style(token, remainder.startswith(':'))}{token}")
        index = end
    return "".join(output)


def highlight_yaml_ranges(
    line: str,
    ranges: tuple[tuple[int, int], ...],
) -> str:
    output: list[str] = []
    cursor = 0
    for start, end in sorted(ranges):
        start = max(cursor, min(start, len(line)))
        end = max(start, min(end, len(line)))
        output.append(highlight_yaml(line[cursor:start]))
        output.append(APPKEY_STYLE)
        output.append(highlight_yaml(line[start:end]))
        output.append(APPKEY_STYLE_END)
        cursor = end
    output.append(highlight_yaml(line[cursor:]))
    return "".join(output)


def overlay_line(
    line: str,
    selected: bool,
    active_pc: bool,
    active_appkey_ranges: tuple[tuple[int, int], ...] = (),
) -> str:
    if selected and active_pc:
        style = PC_SELECTION_STYLE
    elif active_pc:
        style = PC_STYLE
    elif selected:
        style = SELECTION_STYLE
    else:
        style = ""
    return f"{style}{highlight_yaml_ranges(line, active_appkey_ranges)}{RESET}"


def fit(text: str, width: int) -> str:
    return text[:width].ljust(width)


def render(document: Document, state: DemoState, rows: int, columns: int) -> str:
    rows = max(3, rows)
    columns = max(1, columns)
    content_width = max(1, columns - 1)
    body_height = rows - 2
    display_lines = (
        state.editor.lines
        if state.mode == WRITE_MODE and state.editor is not None
        else document.lines
    )
    maximum_viewport = max(0, len(display_lines) - body_height)
    viewport = max(0, min(state.viewport, maximum_viewport))
    context_vm = state.context_vm if state.context_vm is not None else document.active_vm
    active_pc = pc_path(document.data, context_vm)
    selected_line = document.node_by_path.get(state.selection, document.nodes[0]).line
    pc_node = document.node_by_path.get(active_pc)
    pc_line = pc_node.line if pc_node is not None else -1
    active_declarations = (
        active_appkey_spans(document, context_vm)
        if state.mode == RUN_MODE
        else ()
    )
    appkey_ranges_by_line: dict[int, list[tuple[int, int]]] = {}
    for declaration in active_declarations:
        appkey_ranges_by_line.setdefault(declaration.line, []).append(
            (declaration.start, declaration.end)
        )

    status = (
        f" {state.mode}  vm:{format_path(context_vm)}  pc:{format_path(active_pc)} "
        f" lines:{viewport + 1}-{min(len(display_lines), viewport + body_height)}"
    )
    if state.message:
        status += f"  {state.message}"
    output = [
        f"{CLEAR_HOME}{HIDE_CURSOR}{CHROME}{fit(status, content_width)}{RESET}"
    ]
    for row in range(body_height):
        line_index = viewport + row
        line = display_lines[line_index] if line_index < len(display_lines) else ""
        output.append(
            overlay_line(
                fit(line, content_width),
                selected=(
                    state.mode == RUN_MODE and line_index == selected_line
                ),
                active_pc=(
                    state.mode == RUN_MODE and line_index == pc_line
                ),
                active_appkey_ranges=tuple(
                    appkey_ranges_by_line.get(line_index, ())
                ),
            )
        )

    if state.mode == WRITE_MODE and state.editor is not None:
        footer = (
            f" WRITE  Ln {state.editor.cursor_line + 1},"
            f" Col {state.editor.cursor_column + 1}  Esc validate/save/run "
            "arrows move Enter split Backspace/Delete edit Ctrl-Q quit"
        )
    else:
        footer = (
            f" {format_path(state.selection)}  ↑ back ↓ step ← parent → child "
            "Esc write F9 back F10 step F5 continue Ctrl-Q quit"
        )
    output.append(f"{PATH_STYLE}{fit(footer, content_width)}{RESET}")
    rendered = "\r\n".join(output)
    if state.mode == WRITE_MODE and state.editor is not None:
        cursor_row = 2 + state.editor.cursor_line - viewport
        cursor_column = min(content_width, state.editor.cursor_column + 1)
        rendered += f"{SHOW_CURSOR}\x1b[{cursor_row};{cursor_column}H"
    return rendered


def execute_runtime_operation(
    operation: str,
    binary_path: Path,
    target_path: Path,
    *,
    include_source_path: Path | None = None,
) -> dict:
    if operation == "back":
        restore_previous_yaml_snapshot(target_path)
        return load_ordered_document(target_path)

    operations = {
        "step": STEP_FORWARD_OPERATION,
        "start": CONTINUE_OPERATION,
    }
    debug_operation = operations.get(operation)
    if debug_operation is None:
        raise ValueError(f"unsupported HUI runtime operation: {operation}")
    if not execute_debug_operation(
        debug_operation,
        binary_path,
        target_path,
        include_source_path or target_path,
        annotate_pc=False,
    ):
        raise RuntimeError(f"debug operation was not handled: {debug_operation}")
    return load_ordered_document(target_path)


def save_editor_document(target_path: Path, editor: EditorBuffer) -> Document:
    text = editor.text()
    document = build_document(parse_document(text), text)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            prefix=f".{target_path.name}.",
            suffix=".edit",
            dir=target_path.parent,
            delete=False,
        ) as handle:
            handle.write(text)
            temporary_path = Path(handle.name)
        os.replace(temporary_path, target_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return document


def reconcile_document_state(
    document: Document,
    state: DemoState,
) -> DemoState:
    context = state.context_vm
    if context not in vm_paths(document.data):
        context = None
    active_vm = context if context is not None else document.active_vm
    return replace(
        state,
        context_vm=context,
        selection=pc_path(document.data, active_vm),
    )


def run_demo(
    target_path: Path,
    binary_path: Path,
    terminal: Terminal,
    runtime_operation: Callable[[str, Path, Path], dict] = execute_runtime_operation,
) -> None:
    document = load_document(target_path)
    state = DemoState(selection=pc_path(document.data, document.active_vm))

    with terminal:
        while not state.exiting:
            rows, columns = terminal.dimensions()
            terminal.write(render(document, state, rows, columns))
            key = terminal.read_key()
            state, operation = reduce_state(document, state, key, max(1, rows - 2))
            if operation == "save":
                try:
                    if state.editor is None:
                        raise ValueError("write mode requires an editor buffer")
                    document = save_editor_document(target_path, state.editor)
                except Exception as error:
                    state = replace(
                        state,
                        message=f"YAML ERROR: {str(error).splitlines()[0]}",
                    )
                else:
                    state = reconcile_document_state(
                        document,
                        replace(
                            state,
                            mode=RUN_MODE,
                            editor=None,
                            message="",
                        ),
                    )
            elif operation is not None:
                data = runtime_operation(operation, binary_path, target_path)
                document = build_document(data)
                state = reconcile_document_state(
                    document,
                    replace(state, context_vm=None),
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
        debug_target_path = ensure_debug_repo(target_path)
        run_demo(
            debug_target_path,
            resolve_binary_path(args.binary),
            PosixTerminal(),
            partial(
                execute_runtime_operation,
                include_source_path=target_path,
            ),
        )
    except Exception as error:
        print(f"hui demo: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
