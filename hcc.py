#!/usr/bin/env python3
"""Proof-of-concept C-to-Helix compiler.

HCC intentionally targets readable Helix object graphs before efficient code.
The parser and C AST are delegated to pycparser; this file only lowers a small
freestanding C subset into idiomatic Helix forms.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml
from pycparser import c_ast, c_parser, parse_file


class FlowList(list[Any]):
    """YAML list that should render inline for Helix forms and compact vectors."""


class HccDumper(yaml.SafeDumper):
    def ignore_aliases(self, data: Any) -> bool:
        return True


def represent_flow_list(dumper: yaml.SafeDumper, data: FlowList) -> yaml.Node:
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=True)


HccDumper.add_representer(FlowList, represent_flow_list)


def flow_list(*items: Any) -> FlowList:
    return FlowList(items)


HELIX_BUILTINS = {
    "add",
    "append",
    "at",
    "call",
    "copy",
    "div",
    "eval",
    "get",
    "if",
    "list",
    "mod",
    "mul",
    "pop",
    "return",
    "set",
    "show",
    "start",
    "step",
    "sub",
    "while",
}


class HccError(Exception):
    """Raised when the proof-of-concept compiler cannot lower a C construct."""


class HccCompiler:
    def __init__(self) -> None:
        self.function_names: dict[str, str] = {}

    def compile(self, ast: c_ast.FileAST) -> dict[str, Any]:
        self.function_names = self.collect_function_names(ast)
        functions: dict[str, Any] = {}

        for external in ast.ext:
            if isinstance(external, c_ast.FuncDef):
                name, function = self.compile_function(external)
                functions[name] = function
                continue

            if isinstance(external, c_ast.Decl) and isinstance(external.type, c_ast.FuncDecl):
                continue

            raise self.unsupported(external, "top-level declaration")

        if "main" not in self.function_names:
            raise HccError("C source must define int main(...) for executable Helix output")

        return {
            "c": functions,
            "main": flow_list("call", f"c.{self.function_names['main']}", FlowList()),
        }

    def collect_function_names(self, ast: c_ast.FileAST) -> dict[str, str]:
        names = {}
        for external in ast.ext:
            if isinstance(external, c_ast.FuncDef):
                c_name = external.decl.name
                if c_name:
                    names[c_name] = self.helix_function_name(c_name)
        return names

    def helix_function_name(self, c_name: str) -> str:
        if c_name in HELIX_BUILTINS:
            return f"c_{c_name}"
        return c_name

    def compile_function(self, node: c_ast.FuncDef) -> tuple[str, dict[str, Any]]:
        name = node.decl.name
        if not name:
            raise self.unsupported(node, "anonymous function")

        function_type = node.decl.type
        if not isinstance(function_type, c_ast.FuncDecl):
            raise self.unsupported(node.decl, "function declaration")

        self.require_int_type(function_type.type, f"return type for {name}")
        params = self.compile_params(function_type.args)

        function = {
            "kind": "function",
            "params": FlowList(params),
            "body": self.compile_compound(node.body),
        }
        emitted_name = self.function_names[name]
        if emitted_name != name:
            function["c_name"] = name

        return emitted_name, function

    def compile_params(self, args: c_ast.ParamList | None) -> list[str]:
        if args is None:
            return []

        params = []
        for param in args.params:
            if isinstance(param, c_ast.Typename) and self.is_void_type(param.type):
                return []
            if not isinstance(param, c_ast.Decl) or not param.name:
                raise self.unsupported(param, "function parameter")
            self.require_int_type(param.type, f"parameter {param.name}")
            params.append(param.name)
        return params

    def compile_compound(self, node: c_ast.Compound | None) -> list[Any]:
        if node is None or node.block_items is None:
            return []

        body = []
        for item in node.block_items:
            compiled = self.compile_statement(item)
            if compiled is not None:
                body.append(compiled)
        return body

    def compile_statement(self, node: c_ast.Node) -> Any:
        if isinstance(node, c_ast.Compound):
            return self.one_expression_block(node)

        if isinstance(node, c_ast.Decl):
            return self.compile_decl(node)

        if isinstance(node, c_ast.Assignment):
            return self.compile_assignment(node)

        if isinstance(node, c_ast.Return):
            return flow_list("return", self.compile_expr(node.expr) if node.expr else None)

        if isinstance(node, c_ast.FuncCall):
            return self.compile_expr(node)

        if isinstance(node, c_ast.If):
            return self.compile_if(node)

        if isinstance(node, c_ast.While):
            return self.compile_while(node)

        if isinstance(node, c_ast.EmptyStatement):
            return None

        raise self.unsupported(node, "statement")

    def compile_decl(self, node: c_ast.Decl) -> Any:
        if isinstance(node.type, c_ast.FuncDecl):
            return None

        if not node.name:
            raise self.unsupported(node, "anonymous declaration")
        self.require_int_type(node.type, f"local {node.name}")
        return flow_list("set", node.name, self.compile_expr(node.init) if node.init else 0)

    def compile_assignment(self, node: c_ast.Assignment) -> Any:
        if not isinstance(node.lvalue, c_ast.ID):
            raise self.unsupported(node.lvalue, "assignment target")

        name = node.lvalue.name
        value = self.compile_expr(node.rvalue)
        if node.op == "=":
            return flow_list("set", name, value)

        compound_ops = {
            "+=": "add",
            "-=": "sub",
            "*=": "mul",
            "/=": "div",
            "%=": "mod",
        }
        if node.op in compound_ops:
            return flow_list("set", name, flow_list(compound_ops[node.op], name, value))

        raise self.unsupported(node, f"assignment operator {node.op}")

    def compile_if(self, node: c_ast.If) -> Any:
        return flow_list(
            "if",
            self.compile_expr(node.cond),
            self.statement_as_expression(node.iftrue),
            self.statement_as_expression(node.iffalse) if node.iffalse else None,
        )

    def compile_while(self, node: c_ast.While) -> Any:
        return flow_list(
            "while",
            self.compile_expr(node.cond),
            self.statement_as_body(node.stmt),
        )

    def statement_as_expression(self, node: c_ast.Node) -> Any:
        if isinstance(node, c_ast.Compound):
            return self.one_expression_block(node)
        return self.compile_statement(node)

    def one_expression_block(self, node: c_ast.Compound) -> Any:
        body = self.compile_compound(node)
        if len(body) > 1:
            raise self.unsupported(node, "multi-statement block in expression position")
        return body[0] if body else None

    def statement_as_body(self, node: c_ast.Node) -> list[Any]:
        if isinstance(node, c_ast.Compound):
            return self.compile_compound(node)

        statement = self.compile_statement(node)
        return [] if statement is None else [statement]

    def compile_expr(self, node: c_ast.Node | None) -> Any:
        if node is None:
            return None

        if isinstance(node, c_ast.Constant):
            if node.type != "int":
                raise self.unsupported(node, f"constant type {node.type}")
            return int(node.value, 0)

        if isinstance(node, c_ast.ID):
            return node.name

        if isinstance(node, c_ast.BinaryOp):
            return self.compile_binary_op(node)

        if isinstance(node, c_ast.UnaryOp):
            return self.compile_unary_op(node)

        if isinstance(node, c_ast.Assignment):
            return self.compile_assignment(node)

        if isinstance(node, c_ast.FuncCall):
            return self.compile_call(node)

        if isinstance(node, c_ast.Cast):
            self.require_int_type(node.to_type.type, "cast target")
            return self.compile_expr(node.expr)

        if isinstance(node, c_ast.TernaryOp):
            return flow_list(
                "if",
                self.compile_expr(node.cond),
                self.compile_expr(node.iftrue),
                self.compile_expr(node.iffalse),
            )

        raise self.unsupported(node, "expression")

    def compile_binary_op(self, node: c_ast.BinaryOp) -> Any:
        binary_ops = {
            "+": "add",
            "-": "sub",
            "*": "mul",
            "/": "div",
            "%": "mod",
        }
        if node.op not in binary_ops:
            raise self.unsupported(node, f"binary operator {node.op}")
        return flow_list(
            binary_ops[node.op],
            self.compile_expr(node.left),
            self.compile_expr(node.right),
        )

    def compile_unary_op(self, node: c_ast.UnaryOp) -> Any:
        if node.op == "+":
            return self.compile_expr(node.expr)
        if node.op == "-":
            return flow_list("sub", 0, self.compile_expr(node.expr))

        raise self.unsupported(node, f"unary operator {node.op}")

    def compile_call(self, node: c_ast.FuncCall) -> Any:
        if not isinstance(node.name, c_ast.ID):
            raise self.unsupported(node.name, "function expression")

        args = []
        if node.args:
            args = [self.compile_expr(expr) for expr in node.args.exprs]

        return flow_list(
            "call",
            self.function_names.get(node.name.name, node.name.name),
            FlowList(args),
        )

    def require_int_type(self, node: c_ast.Node, context: str) -> None:
        if not self.is_int_type(node):
            raise self.unsupported(node, f"non-int {context}")

    def is_int_type(self, node: c_ast.Node) -> bool:
        while isinstance(node, (c_ast.PtrDecl, c_ast.ArrayDecl)):
            return False

        if isinstance(node, c_ast.TypeDecl):
            return self.is_int_type(node.type)

        if isinstance(node, c_ast.IdentifierType):
            return node.names == ["int"]

        return False

    def is_void_type(self, node: c_ast.Node) -> bool:
        if isinstance(node, c_ast.TypeDecl):
            return self.is_void_type(node.type)
        if isinstance(node, c_ast.IdentifierType):
            return node.names == ["void"]
        return False

    def unsupported(self, node: c_ast.Node | None, context: str) -> HccError:
        location = ""
        if node is not None and getattr(node, "coord", None):
            location = f" at {node.coord}"
        return HccError(f"unsupported {context}{location}")


def parse_c_source(path: Path, use_cpp: bool) -> c_ast.FileAST:
    if use_cpp:
        return parse_file(str(path), use_cpp=True)

    parser = c_parser.CParser()
    return parser.parse(path.read_text(encoding="utf-8"), filename=str(path))


def compile_path(path: Path, use_cpp: bool) -> dict[str, Any]:
    return HccCompiler().compile(parse_c_source(path, use_cpp))


def write_output(program: dict[str, Any], output_path: Path | None) -> None:
    text = yaml.dump(program, Dumper=HccDumper, sort_keys=False)
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


if __name__ == "__main__":
    raise SystemExit(main())
