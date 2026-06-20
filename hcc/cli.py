"""Command-line interface for HCC."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from hcc.compiler import HccError, compile_path, dump_program


def write_output(program: dict[str, object], output_path: Path | None) -> None:
    text = dump_program(program)
    if output_path is None:
        sys.stdout.write(text)
        return

    output_path.write_text(text, encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compile a small freestanding C subset into readable Helix YAML.",
    )
    parser.add_argument("source", type=Path, help="C source file to compile")
    parser.add_argument("-o", "--output", type=Path, help="output YAML path")
    parser.add_argument(
        "--cpp",
        action="store_true",
        help="ask pycparser to run the platform C preprocessor before parsing",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        write_output(compile_path(args.source, args.cpp), args.output)
    except HccError as error:
        print(f"hcc: {error}", file=sys.stderr)
        return 1
    return 0
