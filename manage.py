#!/usr/bin/env python3
"""Canonical Typer/Rich command surface for Helix repository workflows."""

from pathlib import Path

import typer

from scripts import operations


app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
    rich_markup_mode="rich",
    help="Build, test, and operate Helix from one repository entrypoint.",
)


def dispatch(arguments: list[str]) -> None:
    """Delegate command policy to the existing operations module."""
    try:
        operations.main(arguments, script_name="manage.py")
    except SystemExit as error:
        raise typer.Exit(code=error.code if isinstance(error.code, int) else 1) from error


@app.callback()
def default(ctx: typer.Context) -> None:
    """Run the complete build, analysis, and native-test workflow."""
    if ctx.invoked_subcommand is None:
        dispatch([])


@app.command()
def build(
    lib: bool = typer.Option(False, "--lib"),
    no_dynamic_libraries: bool = typer.Option(False, "--no-dynamic-libraries"),
    no_cpp_linenums: bool = typer.Option(False, "--no-cpp-linenums"),
    optimize_size: bool = typer.Option(False, "--optimize-size"),
) -> None:
    """Build the runtime and optional native modules."""
    arguments = ["build"]
    arguments += ["--lib"] if lib else []
    arguments += ["--no-dynamic-libraries"] if no_dynamic_libraries else []
    arguments += ["--no-cpp-linenums"] if no_cpp_linenums else []
    arguments += ["--optimize-size"] if optimize_size else []
    dispatch(arguments)


def test_arguments(command: str, fail_fast: bool, valgrind: bool) -> list[str]:
    arguments = [command]
    arguments += ["--fail-fast"] if fail_fast else []
    arguments += ["--valgrind"] if valgrind else []
    return arguments


@app.command("cpp-test")
def cpp_test(
    fail_fast: bool = typer.Option(False, "--fail-fast"),
    valgrind: bool = typer.Option(False, "--valgrind"),
) -> None:
    """Build and run native runtime fixtures."""
    dispatch(test_arguments("cpp-test", fail_fast, valgrind))


@app.command("hcc-test")
def hcc_test(
    fail_fast: bool = typer.Option(False, "--fail-fast"),
    valgrind: bool = typer.Option(False, "--valgrind"),
) -> None:
    """Build and run HCC fixtures."""
    dispatch(test_arguments("hcc-test", fail_fast, valgrind))


@app.command("hui-test")
def hui_test(
    fail_fast: bool = typer.Option(False, "--fail-fast"),
) -> None:
    """Run deterministic literal-YAML HUI tests."""
    dispatch(["hui-test", *(["--fail-fast"] if fail_fast else [])])


@app.command("hui-demo")
def hui_demo(
    target: Path | None = typer.Argument(None),
    binary: Path | None = typer.Option(None, "--binary"),
) -> None:
    """Launch the interactive literal-YAML HUI."""
    arguments = ["hui-demo"]
    arguments += [str(target)] if target is not None else []
    arguments += ["--binary", str(binary)] if binary is not None else []
    dispatch(arguments)


if __name__ == "__main__":
    app()
