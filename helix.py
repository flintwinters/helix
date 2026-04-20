import argparse
from pathlib import Path
from typing import Any

import yaml
from rich import print as rprint


BUILTINS = {"add", "find", "eval", "list", "set", "show", "start"}


def load_program(path: Path) -> dict[str, Any]:
    program = yaml.safe_load(path.read_text())
    if not isinstance(program, dict):
        raise ValueError("top-level YAML document must be a mapping")
    return program


def resolve_path(current: Any, parts: list[str], name: str) -> Any:
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f'failed to resolve "{name}"')
        current = current[part]
    return current


def resolve(program: dict[str, Any], name: str) -> Any:
    if name in BUILTINS:
        return name
    if name in program:
        return program[name]
    if ":" in name:
        return resolve_path(program, name.split(":"), name)
    raise ValueError(f'failed to resolve "{name}"')


def evaluate(node: Any, program: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return evaluate_vector(node, program)
    if isinstance(node, str):
        return resolve(program, node)
    return node


def evaluate_vector(items: list[Any], program: dict[str, Any]) -> Any:
    if not items:
        raise ValueError("cannot evaluate an empty vector")

    actor = evaluate(items[0], program)
    arguments = items[1:]

    if actor == "add":
        if len(arguments) != 2:
            raise ValueError('builtin "add" expects exactly two arguments')
        left = evaluate(arguments[0], program)
        right = evaluate(arguments[1], program)
        if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
            raise ValueError('builtin "add" expects numeric arguments')
        return left + right

    if actor == "find":
        if len(arguments) != 1 or not isinstance(arguments[0], str):
            raise ValueError('builtin "find" expects exactly one string argument')
        return resolve(program, arguments[0])

    if actor == "eval":
        if len(arguments) != 1:
            raise ValueError('builtin "eval" expects exactly one argument')
        return evaluate(evaluate(arguments[0], program), program)

    if actor == "list":
        return [evaluate(argument, program) for argument in arguments]

    if actor == "show":
        if len(arguments) != 1:
            raise ValueError('builtin "show" expects exactly one argument')
        return evaluate(arguments[0], program)

    if actor == "set":
        if len(arguments) != 2 or not isinstance(arguments[0], str):
            raise ValueError('builtin "set" expects a string and a value')
        value = evaluate(arguments[1], program)
        program[arguments[0]] = value
        return value

    if actor == "start":
        if len(arguments) != 1 or not isinstance(arguments[0], str):
            raise ValueError('builtin "start" expects exactly one string argument')
        vm = resolve(program, arguments[0])
        if not isinstance(vm, dict):
            raise ValueError('builtin "start" expects a VM mapping argument')
        if "main" not in vm:
            raise ValueError('builtin "start" expects a VM with a main entrypoint')
        return evaluate(vm["main"], vm)

    raise ValueError("vector actor did not resolve to a builtin")


def render_vm_state(program: dict[str, Any]) -> None:
    rprint("[bold green]vm:[/bold green]")
    print(yaml.safe_dump(program, sort_keys=False), end="")


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
    try:
        program = load_program(Path(args.program))
        entrypoint = program["eval"]
        evaluate(entrypoint, program)
    except (KeyError, OSError, ValueError) as error:
        rprint(f"[bold red]error:[/bold red] {error}")
        return 1

    render_vm_state(program)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
