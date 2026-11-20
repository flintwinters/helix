"""Command discovery and parsing; execution stays in manage.py."""
import argparse
import textwrap

from tooling.output import DEFAULT_FORMAT, emit


class AgentParser(argparse.ArgumentParser):
    """Scoped text discovery by default; complete structured reports on request."""

    output_format = DEFAULT_FORMAT

    def print_help(self, file=None):
        if self.output_format == "text":
            return super().print_help(file)
        emit({"command": "help", "status": "ok", **getattr(self, "discovery", {}),
              "tree": describe(self)}, stream=file)

    def error(self, message):
        if self.output_format == "text":
            return super().error(message)
        import sys

        emit({"status": "error", "exit_code": 2,
              "error": {"kind": "usage", "message": message},
              "usage": self.format_usage().strip()}, stream=sys.stderr)
        self.exit(2)

DEFAULT_TEST_JOBS = 4

SUITES = {
    "agent_bench": "Offline agent benchmark contracts, independent oracles and usage accounting",
    "yaml_model": "Representation-independent explicit syntax, data roundtrip and deterministic effects",
    "static": "Non-executing checks, canonical formatting, schema and LSP protocol",
    "interactive": "REPL, dependency watch and local installation",
    "inspection": "Stepping, trace streams and observational saved-state summaries",
    "nginx_types": "Pure class realization, defaults and provenance feeding the existing nginx lifecycle",
    "target_coordination": "Cross-journal target exclusion and persistent crash uncertainty for cooperating controllers",
    "secret_effects": "Opaque host secret inputs, suppressed captures, fresh grants and safe durable reconciliation",
    "durable_effects": "Disk effect recovery, controller death, uncertain-outcome reconciliation and fresh grants",
    "snapshots": "Fresh-process executable restoration, editable locals, imported sources and malformed snapshots",
    "long_commands": "Bounded command deadlines, capture, progress, retry classification and process-group cancellation",
    "ssh_host": "Real OpenSSH remote commands: host-key verification, exact argument transport and classified connection, authentication and privilege failures",
    "effects": "Capability-controlled host effects, deterministic inputs, failures and pending requests",
    "startup": "Build-free discovery stays independent of execution and test-only imports",
    "type_time": "First-class type reflection, pure phase isolation, errors and shared resource bounds",
    "docker_facts": "Opt-in pinned Ubuntu 26.10 local and real SSH facts acceptance",
    "host_facts": "Typed explicit local/SSH observations, custom facts and reviewed dependencies",
    "host_inventory": "Pure typed host inventory, explicit override provenance, validated dynamic inputs and target selection",
    "resource_composition": "Validated resource dependencies, coalesced change handlers, failure gating and typed pinned modules",
    "provider_contract": "Typed provider lifecycle, pure previews, explicit ownership and independently verified mutations",
    "command_provider": "Guarded command resources, explicit result predicates, fresh reviews and real subprocess postconditions",
    "ssh_nginx": "Opt-in disposable SSH nginx convergence, review, drift and durable recovery acceptance",
    "docker_nginx": "Opt-in Docker nginx convergence, drift and failure acceptance against independently observed Linux state",
    "docker_linux": "Opt-in Docker Debian resources and mutually configuring Helix peers, against independently observed state",
    "golden": "Exact YAML states, deterministic evaluation, and finished-root round trips",
    "robustness": "Damaged variants of every golden input remain structured states",
    "harness": "Test harness comparators, discovery, timeouts, and crash detection",
    "cli": "Runtime and project CLI contracts, imports, inventory, and recovery",
    "source_reflection": "Authoritative syntax reflection, typed identities, lexical bindings and bounded type evidence",
    "search": "Composable source reflection, streaming discovery, structural queries and explicit probes",
    "pipeline": "helix exec inline/stdin execution, raw strings, YAML/JSON results, errors and jq/yq shell pipes",
    "generated": "Fixed-seed arithmetic oracles, collection laws, and serialization",
    "dogfood": "Standalone Helix test reports, collection laws, and runner failure contracts",
    "errors": "Navigable function definitions, imported call chains, caught errors, causes and correction hints",
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
    "Project reports default to concise text; "
    "--format=json before the command selects complete versioned reports. "
    "Runtime commands build automatically and emit YAML on stdout. "
    "Exit codes: 0 success, 1 evaluation/test failure, 2 invalid usage or unreadable input "
    "(build and forwarded tools retain their own codes). "
    "Paths and stdin imports resolve from the repository root. "
    "Generated artifacts live in runtime/. "
    "Runtime invocations are logged to runtime/usage.sqlite3 (inspect usage; HELIX_USAGE_LOG=0 disables, HELIX_USAGE_DB relocates). "
    "HELIX_TEST_TIMEOUT controls test interpreter timeouts (seconds; default 10); "
    "CARGO_TARGET_DIR selects Cargo build output."
)


def parser():
    """Build one canonical tree and derive compatibility shortcuts from its leaves."""
    root = AgentParser(
        description="Build, execute, inspect, and verify Helix.",
        epilog=textwrap.fill(CONTRACT, width=88), formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    root.add_argument("--help-json", action="store_true", help="print the complete command/argument tree as JSON without building")
    root.add_argument("--format", choices=("json", "text"), default=DEFAULT_FORMAT,
                      help="project report format (default: %(default)s); place before the command; runtime/forwarded output keeps its own format")
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
        "check": "Parse and statically check without executing (use --evaluate for collected runtime errors)",
        "recover": "Evaluate with editable pure recovery source on failure",
        "resume": "Retry edited recovery source, preserving completed root fields",
    }.items():
        leaf = command(runtime, name, description, ["runtime", name], f"runtime {name} - < program.yaml")
        leaf.add_argument("file", metavar="FILE", help="YAML file, or - for stdin; relative to repository root")
    leaves["check"].add_argument("--evaluate", action="store_true", help="execute and collect independent runtime errors")
    for name, description in {
        "fmt": "Emit canonical YAML source without executing",
        "step": "Inspect a program after a bounded number of transitions",
        "system-snapshot": "Form promised participants and save their coordinated execution state",
        "system-restore": "Restore all promised participants and communication together",
        "snapshot": "Save executable state after a bounded number of transitions",
        "restore": "Restore executable state; optionally save after more transitions",
        "checkpoint": "Inspect durable effects and recovery action without execution",
        "target-release": "Acknowledge independently established target quiescence after recovery",
        "trace": "Stream evaluation transitions as YAML documents",
        "watch": "Re-run when program or transitive import contents change",
    }.items():
        leaf = command(runtime, name, description, ["runtime", name], f"runtime {name} program.yaml")
        file_help = {"system-snapshot": "participant source manifest, or - for stdin",
                     "system-restore": "complete promised-system checkpoint, or - for stdin"}.get(name, "source file (or - except for watch)")
        leaf.add_argument("file", metavar="FILE", help=file_help)
        if name in ("snapshot", "restore", "system-snapshot", "system-restore"):
            leaf.add_argument("--steps", type=int, default=0 if name in ("snapshot", "system-snapshot") else None, help="transitions before saving; restore runs to completion when omitted")
        if name == "step":
            leaf.add_argument("--steps", type=int, default=1, help="number of transitions (default: 1)")
        if name == "trace":
            leaf.add_argument("--limit", type=int, help="maximum transitions")
        if name == "watch":
            leaf.add_argument("--interval-ms", type=int, default=250, help="poll interval in milliseconds")
            leaf.add_argument("--iterations", type=int, help="exit after this many run attempts")
    reconcile = command(runtime, "reconcile", "Record an independently observed uncertain-effect outcome", ["runtime", "reconcile"], "runtime reconcile checkpoint.json observed.yaml")
    reconcile.add_argument("file", help="effect checkpoint file")
    reconcile.add_argument("outcome", help="exact request and observed ok/error outcome")
    for name, description in {"repl": "Evaluate one YAML document per input line",
                              "lsp": "Serve the Language Server Protocol over stdio",
                              "schema": "Print the program JSON Schema"}.items():
        command(runtime, name, description, ["runtime", name], f"runtime {name}")
    sandbox = command(runtime, "sandbox", "Evaluate with file imports confined to ROOT (- denies file imports)",
                      ["runtime", "sandbox"], "runtime sandbox - program.yaml")
    sandbox.add_argument("root", metavar="ROOT", help="allowed import directory, or - for embedded modules only")
    sandbox.add_argument("file", metavar="FILE", help="YAML file, or - for stdin")
    pipeline = command(runtime, "exec", "Evaluate inline YAML or stdin; emit raw strings or YAML values",
                       ["runtime", "exec"], "runtime exec '\"!call [add, 20, 22]\"' | ./manage.py runtime exec")
    formats = pipeline.add_mutually_exclusive_group()
    formats.add_argument("--json", dest="output_format", action="store_const", const="--json", help="emit JSON, including quoted string results, for jq/yq")
    formats.add_argument("--yaml", dest="output_format", action="store_const", const="--yaml", help="emit YAML, including quoted string results, for yq")
    pipeline.add_argument("--input", help="ordinary-data file, or - for stdin")
    pipeline.add_argument("--file", help="query source file")
    pipeline.add_argument("--stream", action="store_true", help="incremental JSONL list-producing query")
    pipeline.add_argument("source", nargs="?", default="-", metavar="PROGRAM", help="inline YAML, or - for stdin (default)")
    search = command(runtime, "search", "Stream source index, parse source, or explicitly probe selected functions",
                     ["runtime", "search"], "runtime search index std --no-std")
    search.add_argument("extra", nargs=argparse.REMAINDER, help="index [PATH ...] | parse FILE | probe --cases FILE [--root PATH]")
    inline = command(runtime, "eval", "Evaluate inline YAML and emit VM state",
                     ["runtime", "eval"], "runtime eval '!call [add, 20, 22]'")
    inline.add_argument("source", metavar="SOURCE", help="YAML program quoted as one shell argument; use -- before a leading dash")
    inline.add_argument("--check", action="store_true", help="collect independent evaluation errors")
    perform = command(runtime, "perform", "Execute authorized host effects; state on stderr, program output on stdout",
                      ["runtime", "perform"], "runtime perform --allow-command /bin/echo program.yaml")
    perform.add_argument("args", nargs=argparse.REMAINDER,
                         help="caller grants and command policy; runtime perform -- --help lists all flags")
    raw = command(runtime, "cli", "Build and forward arguments verbatim to the Rust CLI",
                  ["runtime", "cli"], "runtime cli std math")
    raw.add_argument("args", nargs=argparse.REMAINDER, help="Rust CLI arguments; use -- to forward --help")
    inspect = group("inspect", "Discover standard library source and repository structure")
    state = command(inspect, "state", "Summarize a saved state without executing it",
                    ["inspect", "state"], "inspect state state.yaml")
    state.add_argument("file", metavar="FILE", help="state file, or - for stdin")
    modules = command(inspect, "std", "Browse the standard library: modules, then one module's signatures, then one export",
                      ["inspect", "std"], "inspect std list.find")
    modules.add_argument("module", nargs="?", metavar="MODULE[.EXPORT]", help="module, optionally prefixed with std:, or one export")
    modules.add_argument("--source", dest="whole", action="store_true", help="print the module's complete source")
    command(inspect, "inventory", "Report source files grouped through Helix; save replay artifacts in runtime/",
            ["inspect", "inventory"], "inspect inventory")
    usage = command(inspect, "usage", "Summarize or query the log of runtime invocations made through manage.py",
                    ["inspect", "usage"], "inspect usage --sql 'SELECT command, COUNT(*) FROM calls GROUP BY 1'")
    usage.add_argument("--sql", help="read-only SQL over table calls (see --schema)")
    usage.add_argument("--schema", action="store_true", help="print the calls table definition")
    usage.add_argument("--limit", type=int, default=10, help="recent calls to list in the summary (default: %(default)s)")
    migration = command(runtime, "migrate-syntax", "Convert legacy YAML source offline to explicit syntax",
                        ["runtime", "migrate-syntax"], "runtime migrate-syntax --write std tests/cases")
    migration.add_argument("paths", nargs="+", metavar="PATH", help="source files or directories (expected outputs excluded)")
    migration.add_argument("--write", action="store_true", help="rewrite source files; default only reports changes")
    formatting = command(runtime, "format", "Check or rewrite source files with structured width-aware YAML and retained comments",
                         ["runtime", "format"], "runtime format --write std")
    formatting.add_argument("paths", nargs="+", metavar="PATH", help="YAML files or recursively searched directories")
    formatting.add_argument("--write", action="store_true", help="rewrite changed files; default checks formatting")
    quality = group("quality", "Run regression suites and structural lint")
    docker = command(quality, "docker-nginx", "Discover Docker acceptance scenarios or clean a retained run",
                     ["quality", "docker-nginx"], "quality docker-nginx describe")
    docker.add_argument("action", choices=("describe", "cleanup"))
    docker.add_argument("run_id", nargs="?", help="32-character run ID required for cleanup; only matching labeled resources are removed")
    docker.epilog += "\nRun: ./manage.py test --suite docker_nginx. HELIX_DOCKER_NGINX_SCENARIO selects one scenario; HELIX_DOCKER_NGINX_REPEAT defaults to 2; HELIX_DOCKER_NGINX_KEEP_FAILED=1 retains failed containers."
    audit = command(quality, "migration-audit", "Compare migrated source outcomes against existing golden expectations",
            ["quality", "migration-audit"], "quality migration-audit")
    audit.add_argument("--baseline", help="read golden expectations from a Git revision instead of the working tree")
    bench = command(quality, "agent-bench", "Measure repeated one-shot coding with objective oracles and CLI timings",
                    ["quality", "agent-bench"], "quality agent-bench run --trials 10")
    bench.add_argument("action", choices=("list", "models", "run", "score", "report"))
    bench.add_argument("--mode", choices=("one-shot", "cli"), default="cli", help="CLI-enabled bounded agent or a single completion without tools")
    bench.add_argument("--max-turns", type=int, default=16, help="maximum model requests per CLI-enabled attempt")
    bench.add_argument("--max-tool-calls", type=int, default=32, help="maximum workspace/CLI/submission calls per CLI-enabled attempt")
    bench.add_argument("--jobs", type=int, default=4, help="independent generation workers; evaluation and timings remain isolated")
    bench.add_argument("--model", default="openai/gpt-6.1-luna", help="exact OpenRouter model ID")
    bench.add_argument("--trials", type=int, default=10, help="independent completions per task")
    bench.add_argument("--task", action="append", help="task ID; repeat to select several")
    bench.add_argument("--samples", type=int, default=3, help="timing samples after one warmup")
    bench.add_argument("--max-tokens", type=int, default=4096, help="completion token ceiling per model request")
    bench.add_argument("--temperature", type=float, default=0.7)
    bench.add_argument("--directory", help="artifact directory; required for score, generated for run")
    bench.add_argument("--audit", action="store_true", help="report saved-file/test/evaluation fingerprint integrity without running models or code")
    bench.add_argument("--replay", action="store_true", help="report the saved replay evaluation instead of original timings")
    bench.add_argument("--dry-run", action="store_true", help="validate references and save prompts without API calls")

    testing = command(quality, "test", "Run regression suites or discover their contracts",
                      ["quality", "test"], "quality test --suite golden --suite cli")
    testing.add_argument("--bless", action="store_true", help="rewrite golden expectations; requires only golden or default suite selection")
    testing.add_argument("--suite", action="append", choices=SUITES, metavar="SUITE", help="repeat to select suites; default: all except explicitly reported opt-in Docker acceptance")
    testing.add_argument("--case", action="append", metavar="GLOB",
                         help="filter fixture stems or repository-relative paths; repeat for a union; requires golden/robustness suites only")
    testing.add_argument("--rerun-failed", metavar="REPORT.json",
                         help="rerun failed suites and recorded case selections from a versioned JSON report; no inherited blessing or Docker authorization")
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
    testing.epilog += "\nDiscover suite contracts: ./manage.py test --describe"
    testing.epilog += "\nRerun failures: ./manage.py test --rerun-failed runtime/test-report.json"
    testing.formatter_class = argparse.RawDescriptionHelpFormatter
    root.discovery = {"shortcuts": aliases, "suites": SUITES}
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
    output_format = DEFAULT_FORMAT
    if argv and (argv[0] == "--format" or argv[0].startswith("--format=")):
        if argv[0] == "--format":
            if len(argv) < 2:
                root.error("--format requires json or text")
            output_format, argv = argv[1], argv[2:]
        else:
            output_format, argv = argv[0].split("=", 1)[1], argv[1:]
        if output_format not in ("json", "text"):
            root.error("--format must be json or text")

    def configure(node):
        node.output_format = output_format
        for action in node._actions:
            if isinstance(action, argparse._SubParsersAction):
                for child in action.choices.values():
                    configure(child)

    configure(root)
    if argv == ["--help-json"]:
        root.output_format = "json"
        root.print_help()
        return None
    if argv and argv[0] in aliases:
        argv = [*aliases[argv[0]], *argv[1:]]
    # Forward unknown tool flags intact, while keeping local help discoverable.
    for name in ("cli", "perform", "lint"):
        path = aliases[name]
        if argv[:len(path)] == path:
            extra = argv[len(path):]
            if extra in (["--help"], ["-h"]):
                leaves[name].print_help()
                return None
            if extra[:1] == ["--"]:
                extra = extra[1:]
            return argparse.Namespace(command=name, extra=extra, format=output_format)
    args = root.parse_args(argv)
    args.format = output_format
    if args.help_json:
        root.error("--help-json must be used alone")
    if args.command is None:
        args.help_parser.print_help()
        return None
    if args.command == "test" and args.jobs < 1:
        args.help_parser.error("--jobs must be positive")
    if args.command == "test" and args.rerun_failed is not None and (
            args.suite or args.case or args.bless or args.list or args.describe):
        args.help_parser.error("--rerun-failed cannot be combined with --suite, --case, --bless, --list or --describe")
    if args.command == "test" and args.case and (not args.suite or set(args.suite) - {"golden", "robustness"}):
        args.help_parser.error("--case requires explicit --suite golden and/or --suite robustness")
    if args.command == "test" and args.bless and args.suite and set(args.suite) != {"golden"}:
        args.help_parser.error("--bless requires --suite golden (or no suite selection)")
    return args
