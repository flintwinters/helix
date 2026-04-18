from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml
except ModuleNotFoundError:
    yaml = None

try:
    from rich import print as rprint
except ModuleNotFoundError:
    rprint = print


class Cell:
    def eval(self, vm: "MapCell") -> "Cell":
        return self

    def find(self, vm: "MapCell", key: str) -> "Cell":
        return ErrorCell(f'key "{key}" is not available in this cell')

    def to_python(self) -> Any:
        raise NotImplementedError


@dataclass(slots=True)
class ErrorCell(Cell):
    message: str

    def eval(self, vm: "MapCell") -> "Cell":
        return self

    def find(self, vm: "MapCell", key: str) -> "Cell":
        return self

    def to_python(self) -> Any:
        return {"error": self.message}


@dataclass(slots=True)
class ScalarCell(Cell):
    value: Any

    def to_python(self) -> Any:
        return self.value


@dataclass(slots=True)
class StrCell(ScalarCell):
    value: str

    def eval(self, vm: "MapCell") -> Cell:
        return vm.find(vm, self.value)


@dataclass(slots=True)
class VecCell(Cell):
    items: list[Cell]

    def eval(self, vm: "MapCell") -> Cell:
        if not self.items:
            return ErrorCell("cannot evaluate an empty vector")

        actor = self.items[0].eval(vm)
        if isinstance(actor, ErrorCell):
            return actor
        if not isinstance(actor, BuiltinCell):
            return ErrorCell("vector actor did not resolve to a builtin")

        return actor.call(vm, self.items[1:])

    def to_python(self) -> Any:
        return [item.to_python() for item in self.items]


@dataclass(slots=True)
class MapCell(Cell):
    entries: dict[str, Cell] = field(default_factory=dict)
    builtins: dict[str, Cell] = field(default_factory=dict)
    parent: "MapCell | None" = None

    def find(self, vm: "MapCell", key: str) -> Cell:
        builtin = vm.builtins.get(key)
        if builtin is not None:
            return builtin
        if key in self.entries:
            return self.entries[key]
        if self.parent is not None:
            return self.parent.find(vm, key)
        return ErrorCell(f'failed to resolve "{key}"')

    def eval(self, vm: "MapCell") -> Cell:
        target = self.entries.get("eval")
        if target is None:
            return ErrorCell('map cell is missing an "eval" entrypoint')
        return target.eval(self)

    def to_python(self) -> Any:
        return {key: value.to_python() for key, value in self.entries.items()}


@dataclass(slots=True)
class BuiltinCell(Cell):
    name: str

    def call(self, vm: MapCell, arguments: list[Cell]) -> Cell:
        if self.name == "find":
            return builtin_find(vm, arguments)
        if self.name == "eval":
            return builtin_eval(vm, arguments)
        if self.name == "list":
            return builtin_list(vm, arguments)
        if self.name == "show":
            return builtin_show(vm, arguments)
        return ErrorCell(f'unknown builtin "{self.name}"')

    def to_python(self) -> Any:
        return {"builtin": self.name}


def builtin_find(vm: MapCell, arguments: list[Cell]) -> Cell:
    if len(arguments) != 1:
        return ErrorCell('builtin "find" expects exactly one argument')
    key_cell = arguments[0]
    if not isinstance(key_cell, StrCell):
        return ErrorCell('builtin "find" expects a string argument')
    return vm.find(vm, key_cell.value)


def builtin_eval(vm: MapCell, arguments: list[Cell]) -> Cell:
    if len(arguments) != 1:
        return ErrorCell('builtin "eval" expects exactly one argument')
    target = arguments[0].eval(vm)
    if isinstance(target, ErrorCell):
        return target
    return target.eval(vm)


def builtin_list(vm: MapCell, arguments: list[Cell]) -> Cell:
    values = [argument.eval(vm) for argument in arguments]
    for value in values:
        if isinstance(value, ErrorCell):
            return value
    return VecCell(values)


def builtin_show(vm: MapCell, arguments: list[Cell]) -> Cell:
    if len(arguments) != 1:
        return ErrorCell('builtin "show" expects exactly one argument')
    return arguments[0].eval(vm)


def build_cell(node: Any, parent: MapCell | None = None) -> Cell:
    if isinstance(node, dict):
        cell = MapCell(parent=parent)
        for key, value in node.items():
            if key == "parent":
                continue
            cell.entries[key] = build_cell(value, cell)

        parent_ref = node.get("parent")
        if isinstance(parent_ref, str):
            parent_cell = cell.entries.get(parent_ref)
            if isinstance(parent_cell, MapCell):
                cell.parent = parent_cell
        elif isinstance(parent_ref, dict):
            parent_cell = build_cell(parent_ref, cell)
            if isinstance(parent_cell, MapCell):
                cell.parent = parent_cell
        return cell

    if isinstance(node, list):
        return VecCell([build_cell(item, parent) for item in node])

    if isinstance(node, str):
        return StrCell(node)

    return ScalarCell(node)


def attach_builtins(root: MapCell) -> None:
    root.builtins.update(
        {
        "find": BuiltinCell("find"),
        "eval": BuiltinCell("eval"),
        "list": BuiltinCell("list"),
        "show": BuiltinCell("show"),
        }
    )


def load_program(path: Path) -> MapCell:
    if yaml is None:
        raise RuntimeError('missing dependency: install "pyyaml" to load YAML programs')

    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("top-level YAML document must be a mapping")

    root = build_cell(data)
    if not isinstance(root, MapCell):
        raise ValueError("top-level YAML document did not produce a map cell")

    attach_builtins(root)
    return root


def render_result(result: Cell) -> None:
    if isinstance(result, ErrorCell):
        rprint(f"[bold red]error:[/bold red] {result.message}")
        return
    rprint("[bold green]result:[/bold green]")
    rprint(result.to_python())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a small Helix YAML demo.")
    parser.add_argument(
        "program",
        nargs="?",
        default="helix_demo.yaml",
        help="Path to the YAML program to evaluate.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    program_path = Path(args.program)
    try:
        root = load_program(program_path)
        result = root.eval(root)
    except (OSError, RuntimeError, ValueError) as error:
        rprint(f"[bold red]error:[/bold red] {error}")
        return 1

    render_result(result)
    return 0 if not isinstance(result, ErrorCell) else 1


if __name__ == "__main__":
    raise SystemExit(main())
