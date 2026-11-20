//! The CLI's command table: one inventory renders the overview, each command's usage, the
//! structured `--json` catalog and command-name validation, so they cannot drift apart.
use std::process::ExitCode;

use serde_json::{Value as J, json};

use crate::graph::nearest;

pub struct Command {
    pub name: &'static str,
    pub group: &'static str,
    /// `(arguments, summary)` for each accepted form.
    pub forms: &'static [(&'static str, &'static str)],
    /// Behaviour shared by every form, shown with the command's usage.
    pub notes: &'static [&'static str],
}

const fn entry(name: &'static str, group: &'static str, forms: &'static [(&'static str, &'static str)]) -> Command {
    Command { name, group, forms, notes: &[] }
}

/// Every command, grouped in the order a newcomer needs them: learn, write and run, observe,
/// recover. `--help` output follows this order.
pub const COMMANDS: &[Command] = &[
    entry("help", "learn", &[("[COMMAND] [--json]", "this overview, or one command's forms; also `helix COMMAND --help`")]),
    entry("docs", "learn", &[
        ("[DOC [SECTION]] [--json]", "reference documents: list, a document's section tree, then one section"),
        ("PATH.md [SECTION] [--json]", "the same navigation for any Markdown file"),
    ]),
    entry("builtins", "learn", &[("[NAME] [--json]", "builtin actors by family with arity, then one builtin's documentation")]),
    entry("std", "learn", &[
        ("[MODULE[.EXPORT]] [--json]", "browse the standard library: modules, a module's signatures, one export"),
        ("MODULE --source", "print a module's complete source"),
    ]),
    entry("schema", "learn", &[("", "print program JSON Schema")]),
    entry("check", "write and run", &[("[--evaluate] <file.yaml | ->", "static checks; --evaluate collects runtime errors")]),
    entry("fmt", "write and run", &[("<file.yaml | ->", "canonical source on stdout")]),
    entry("run", "write and run", &[("<file.yaml | ->", "emit full VM state")]),
    Command {
        name: "exec",
        group: "write and run",
        forms: &[("[--stream] [--input DATA] [--file QUERY] [--json | --yaml] [PROGRAM | -]", "inline source or stdin; default raw strings, otherwise YAML")],
        notes: &["exec failures emit VM state to stderr, leave stdout empty, and exit 1.", "Use exec -- PROGRAM to escape reserved arguments."],
    },
    entry("sandbox", "write and run", &[("<root | -> <file.yaml | ->", "restrict transitive file imports")]),
    entry("repl", "write and run", &[("", "evaluate one YAML document per line")]),
    entry("watch", "write and run", &[("FILE [--interval-ms N] [--iterations N]", "re-run on dependency changes")]),
    entry("perform", "write and run", &[("[grants] <file> [-- args...]", "explicit host effects; state to stderr; `helix perform --help` lists grants")]),
    entry("step", "observe", &[("FILE [--steps N]", "inspect after N transitions")]),
    entry("trace", "observe", &[("FILE [--limit N]", "stream transition inspections")]),
    entry("inspect", "observe", &[("<state.yaml | ->", "summarize saved state without execution")]),
    entry("search", "observe", &[
        ("index [PATH ...] [--library PATH_OR_STD_MODULE] [--no-std] [--no-default-excludes]", "stream a source index"),
        ("parse FILE_OR_STDIN", "parse source for search"),
        ("probe --cases FILE [--harness FILE] [--root PATH] [--max-steps N]", "explicitly probe selected functions"),
    ]),
    entry("lsp", "observe", &[("", "serve Language Server Protocol on stdin/stdout")]),
    entry("recover", "recover", &[("<file.yaml | ->", "save a pure recovery checkpoint on failure")]),
    entry("resume", "recover", &[("<state.yaml | ->", "run edited recovery source")]),
    entry("snapshot", "recover", &[("FILE [--steps N]", "save executable state after N transitions")]),
    entry("restore", "recover", &[("FILE [--steps N]", "restore and run; --steps emits another checkpoint")]),
    entry("system-snapshot", "recover", &[("FILE [--steps N]", "form and save coordinated promised participants")]),
    entry("system-restore", "recover", &[("FILE [--steps N]", "restore all participants and communication")]),
    entry("checkpoint", "recover", &[("FILE", "inspect durable effects and recovery action without execution")]),
    entry("reconcile", "recover", &[("CHECKPOINT OUTCOME", "record an observed uncertain-effect outcome")]),
    entry("target-release", "recover", &[("FILE", "acknowledge independently established target quiescence")]),
];

/// Where a newcomer starts: how to learn the language before writing a program.
const START: &[&str] = &[
    "helix docs language    the language reference as a section tree; read one section at a time",
    "helix builtins         builtin actors (add, get, map, fn, ...) with arity; `helix builtins NAME` for one",
    "helix std              standard library modules; `helix std MODULE` lists signatures",
    "helix check FILE       static errors with corrections, before `helix run FILE`",
];

pub fn names() -> impl Iterator<Item = &'static str> {
    COMMANDS.iter().map(|command| command.name)
}

pub fn find(name: &str) -> Option<&'static Command> {
    COMMANDS.iter().find(|command| command.name == name)
}

impl Command {
    fn lines(&self) -> impl Iterator<Item = (String, &'static str)> + '_ {
        self.forms.iter().map(|(args, summary)| (format!("helix {} {args}", self.name).trim_end().to_string(), *summary))
    }

    /// `usage:` lines for this command alone, then its notes.
    pub fn usage(&self) -> String {
        let forms: Vec<_> = self.lines().map(|(form, summary)| format!("{form}    {summary}")).collect();
        let mut text = format!("usage: {}", forms.join("\n       "));
        if !self.notes.is_empty() {
            text = format!("{text}\n\n{}", self.notes.join("\n"));
        }
        text
    }

    fn json(&self) -> J {
        let forms: Vec<_> = self.lines().map(|(usage, summary)| json!({"usage": usage, "summary": summary})).collect();
        json!({"command": self.name, "group": self.group, "forms": forms, "notes": self.notes})
    }
}

/// `helix help [COMMAND] [--json]`, `helix --help [--json]` and `helix COMMAND --help [--json]`.
pub fn command(args: &[&str]) -> ExitCode {
    let json = args.contains(&"--json");
    let mut rest: Vec<&str> = args.iter().copied().filter(|arg| !matches!(*arg, "--json" | "--help" | "-h")).collect();
    // `helix help --help` asks about `help` itself.
    if rest.is_empty() && args.len() > usize::from(json) {
        rest.push("help");
    }
    match rest.as_slice() {
        [] if json => println!("{}", json!({
            "usage": "helix COMMAND [ARGS]",
            "start": START,
            "commands": COMMANDS.iter().map(Command::json).collect::<Vec<_>>(),
        })),
        [] => println!("{}", overview()),
        [name] => match find(name) {
            Some(found) if json => println!("{}", found.json()),
            Some(found) => println!("{}", found.usage()),
            None => return unknown(name),
        },
        _ => {
            eprintln!("{}", find("help").unwrap().usage());
            return ExitCode::from(2);
        }
    }
    ExitCode::SUCCESS
}

/// Every form grouped by purpose, after the starting points.
pub fn overview() -> String {
    let mut text = String::from("usage: helix COMMAND [ARGS]    `helix help COMMAND` shows one command; add --json for structured output\n\nstart here:\n");
    for line in START {
        text += &format!("  {line}\n");
    }
    let mut group = "";
    for command in COMMANDS {
        if command.group != group {
            group = command.group;
            text += &format!("\n{group}:\n");
        }
        for (form, summary) in command.lines() {
            text += &format!("  {form}    {summary}\n");
        }
    }
    text.trim_end().to_string()
}

/// Reject a command name that no form accepts, naming the closest known command.
pub fn unknown(name: &str) -> ExitCode {
    match nearest(name, names()) {
        Some(close) => eprintln!("unknown command `{name}`; did you mean `{close}`? `helix --help` lists every command"),
        None => eprintln!("unknown command `{name}`; `helix --help` lists every command"),
    }
    ExitCode::from(2)
}
