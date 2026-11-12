"""Command discovery and parsing; execution stays in manage.py."""
import argparse
import json
import textwrap

DEFAULT_TEST_JOBS = 4

SUITES = {
    "startup": "Build-free discovery stays independent of execution and test-only imports",
    "type_time": "First-class type reflection, pure phase isolation, errors and shared resource bounds",
    "golden": "Exact YAML states, deterministic evaluation, and finished-root round trips",
    "robustness": "Damaged variants of every golden input remain structured states",
    "harness": "Test harness comparators, discovery, timeouts, and crash detection",
    "cli": "Runtime and project CLI contracts, imports, inventory, and recovery",
    "pipeline": "helix exec inline/stdin execution, raw strings, YAML results, errors and shell pipes",
    "generated": "Fixed-seed arithmetic oracles, collection laws, and serialization",
    "dogfood": "Standalone Helix test reports, collection laws, and runner failure contracts",
    "errors": "Navigable function definitions, imported call chains, caught errors and causes",
    "imports": "Module resolution, isolation, cycles, and source blame",
    "execution": "Immutable stepping, retained branches, nested machines and explicit continuations",
    "library": "Rust public VM API contracts via cargo test",
    "parser": "YAML syntax, unsupported constructs, paths, and source locations",
    "fuzz": "Bounded fixed-seed token mutations and failure minimization",
    "type_algebra": "Independent nested-type membership oracle, ordered violations and invalid declarations",
    "semantics": "Every builtin result witness and declared arity boundary, Unicode and precise blame",
    "permissions": "Embedded-only and confined imports, transitive denial, path and symlink escapes",
    "mutants": "Compile isolated arithmetic, source-blame and serialization faults; require suite failures",
    "memory": "Linux GNU-time peak RSS and scaling, including constant-state long runs",
    "resources": "Syntax, graph, recursion, iteration, import and computed-growth boundaries",
    "scale": "Large arithmetic maps with execution-time bounds",
    "speed": "Repeated CLI speed benchmarks with warmup, correctness checks and runtime/speed reports",
}

CONTRACT = (
    "Runtime commands build automatically and emit YAML on stdout. "
    "Exit codes: 0 success, 1 evaluation/test failure, 2 invalid usage or unreadable input "
    "(build and forwarded tools retain their own codes). "
    "Paths and stdin imports resolve from the repository root. "
    "Generated artifacts live in runtime/. "
    "HELIX_TEST_TIMEOUT controls test interpreter timeouts (seconds; default 10); "
    "CARGO_TARGET_DIR selects Cargo build output."
)


def parser():
    """Build one canonical tree and derive compatibility shortcuts from its leaves."""
    root = argparse.ArgumentParser(
        description="Build, execute, inspect, and verify Helix.",
        epilog=textwrap.fill(CONTRACT, width=88), formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    root.add_argument("--help-json", action="store_true", help="print the complete command/argument tree as JSON without building")
    aliases = {}
    leaves = {}

    def children(parent):
        parent.set_defaults(help_parser=parent, command=None)
        return parent.add_subparsers(title="commands", metavar="COMMAND")

    top = children(root)

    def group(name, description):
        return children(top.add_parser(name, help=description, description=description))

    def command(parent, name, description, path, example):
        leaf = parent.add_parser(name, help=description, description=description,
                                 epilog=f"Example: ./manage.py {example}")
        leaf.set_defaults(command=name, help_parser=leaf)
        aliases[name] = path
        leaves[name] = leaf
        return leaf

    command(top, "build", "Compile the Rust crate with Cargo", ["build"], "build")
    runtime = group("runtime", "Evaluate YAML programs and work with recovery state")
    for name, description in {
        "run": "Evaluate a YAML file or stdin and emit VM state",
        "check": "Collect independent evaluation errors (uncalled bodies are not checked)",
        "recover": "Evaluate with editable pure recovery source on failure",
        "resume": "Retry edited recovery source, preserving completed root fields",
    }.items():
        leaf = command(runtime, name, description, ["runtime", name], f"runtime {name} - < program.yaml")
        leaf.add_argument("file", metavar="FILE", help="YAML file, or - for stdin; relative to repository root")
    sandbox = command(runtime, "sandbox", "Evaluate with file imports confined to ROOT (- denies file imports)",
                      ["runtime", "sandbox"], "runtime sandbox - program.yaml")
    sandbox.add_argument("root", metavar="ROOT", help="allowed import directory, or - for embedded modules only")
    sandbox.add_argument("file", metavar="FILE", help="YAML file, or - for stdin")
    pipeline = command(runtime, "exec", "Evaluate inline YAML or stdin; emit raw strings or YAML values",
                       ["runtime", "exec"], "runtime exec '\"[add, 20, 22]\"' | ./manage.py runtime exec")
    pipeline.add_argument("source", nargs="?", default="-", metavar="PROGRAM", help="inline YAML, or - for stdin (default)")
    inline = command(runtime, "eval", "Evaluate inline YAML and emit VM state",
                     ["runtime", "eval"], "runtime eval '[add, 20, 22]'")
    inline.add_argument("source", metavar="SOURCE", help="YAML program quoted as one shell argument; use -- before a leading dash")
    inline.add_argument("--check", action="store_true", help="collect independent evaluation errors")
    raw = command(runtime, "cli", "Build and forward arguments verbatim to the Rust CLI",
                  ["runtime", "cli"], "runtime cli std math")
    raw.add_argument("args", nargs=argparse.REMAINDER, help="Rust CLI arguments; use -- to forward --help")
    inspect = group("inspect", "Discover standard library source and repository structure")
    modules = command(inspect, "std", "List standard modules or print one module's source",
                      ["inspect", "std"], "inspect std math")
    modules.add_argument("module", nargs="?", help="module name, optionally prefixed with std:")
    command(inspect, "inventory", "Report source files grouped through Helix; save replay artifacts in runtime/",
            ["inspect", "inventory"], "inspect inventory")
    quality = group("quality", "Run regression suites and structural lint")
    testing = command(quality, "test", "Run regression suites or discover their contracts",
                      ["quality", "test"], "quality test --suite golden --suite cli")
    testing.add_argument("--bless", action="store_true", help="rewrite golden expectations; requires only golden or default suite selection")
    testing.add_argument("--suite", action="append", choices=SUITES, help="repeat to select suites; default: all")
    testing.add_argument("--jobs", type=int, default=DEFAULT_TEST_JOBS, metavar="N",
                         help="golden/robustness case workers (default: %(default)s; 1 runs serially)")
    testing.add_argument("--list", action="store_true", help="list suite names without building")
    testing.add_argument("--describe", action="store_true", help="list suites and their contracts without building")
    lint = command(quality, "lint", "Run hyperlinter (JSON by default); forward remaining arguments",
                   ["quality", "lint"], "quality lint --format=text")
    lint.add_argument("args", nargs=argparse.REMAINDER, help="hyperlinter arguments; use -- to forward --help")
    root.epilog += "\n\nShortcuts (equivalent to the full paths):\n" + "\n".join(
        f"  {name:<10} {' '.join(path)}" for name, path in aliases.items() if len(path) > 1
    )
    testing.epilog += "\n\nSuites:\n" + "\n".join(f"  {name}: {description}" for name, description in SUITES.items())
    testing.formatter_class = argparse.RawDescriptionHelpFormatter
    return root, aliases, leaves


def describe(parser):
    """Derive JSON discovery directly from argparse's command and option definitions."""
    arguments = []
    commands = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            commands = {name: describe(child) for name, child in action.choices.items()}
        else:
            arguments.append({
                "name": action.dest, "flags": action.option_strings, "help": action.help,
                "required": action.required, "nargs": action.nargs,
                "choices": list(action.choices) if action.choices is not None else None,
                "default": action.default,
            })
    return {"description": parser.description, "usage": parser.format_usage().strip(),
            "notes": parser.epilog, "arguments": arguments, "commands": commands}


def parse(argv):
    root, aliases, leaves = parser()
    if argv == ["--help-json"]:
        print(json.dumps({"version": 1, "shortcuts": aliases, "suites": SUITES,
                          "tree": describe(root)}, indent=2))
        return None
    if argv and argv[0] in aliases:
        argv = [*aliases[argv[0]], *argv[1:]]
    # Forward unknown tool flags intact, while keeping local help discoverable.
    for name in ("cli", "lint"):
        path = aliases[name]
        if argv[:len(path)] == path:
            extra = argv[len(path):]
            if extra in (["--help"], ["-h"]):
                leaves[name].print_help()
                return None
            if extra[:1] == ["--"]:
                extra = extra[1:]
            return argparse.Namespace(command=name, extra=extra)
    args = root.parse_args(argv)
    if args.help_json:
        root.error("--help-json must be used alone")
    if args.command is None:
        args.help_parser.print_help()
        return None
    if args.command == "test" and args.jobs < 1:
        args.help_parser.error("--jobs must be positive")
    if args.command == "test" and args.bless and args.suite and set(args.suite) != {"golden"}:
        args.help_parser.error("--bless requires --suite golden (or no suite selection)")
    return args
