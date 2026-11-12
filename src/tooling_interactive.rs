//! Terminal and polling adapters over the existing VM and import linker.
use std::collections::BTreeMap;
use std::io::{self, BufRead, IsTerminal, Write};
use std::path::{Path, PathBuf};
use std::process::ExitCode;
use std::time::Duration;

use crate::{Status, Vm};

/// Shared CLI command inventory, also used by command validation and shell completions.
pub const COMMANDS: &[&str] = &[
    "exec",
    "run",
    "sandbox",
    "recover",
    "resume",
    "check",
    "std",
    "fmt",
    "repl",
    "watch",
    "step",
    "trace",
    "inspect",
    "schema",
    "lsp",
    "completion",
    "perform",
];

const REPL_HELP: &str = "Enter one YAML program per line. Each program has its own lexical scope.\n:begin starts a multiline program; :end evaluates it; :cancel discards it.\n:help shows this help; :quit exits. Results are YAML state documents.";

fn emit_state(vm: &Vm) -> io::Result<()> {
    let mut out = io::stdout().lock();
    writeln!(out, "---\n{}\n...", vm.state_string().trim_end())?;
    out.flush()
}

/// Independent line-oriented programs, with explicit multiline input and recovery after errors.
pub fn repl() -> ExitCode {
    let terminal = io::stdin().is_terminal();
    if terminal {
        eprintln!("Helix REPL. :help for commands; :quit to exit.");
    }
    let mut input = io::stdin().lock();
    let mut block: Option<String> = None;
    let mut failed = false;
    loop {
        if terminal {
            eprint!("{}", if block.is_some() { "... " } else { "helix> " });
            let _ = io::stderr().flush();
        }
        let mut line = String::new();
        match input.read_line(&mut line) {
            Ok(0) => {
                if block.is_some() {
                    eprintln!("unfinished multiline program; use :end to evaluate it");
                    return ExitCode::from(2);
                }
                break;
            }
            Ok(_) => {}
            Err(error) => {
                eprintln!("cannot read REPL input: {error}");
                return ExitCode::from(2);
            }
        }
        let source = match line.trim() {
            ":quit" => break,
            ":help" => {
                eprintln!("{REPL_HELP}");
                continue;
            }
            ":begin" if block.is_none() => {
                block = Some(String::new());
                continue;
            }
            ":cancel" => {
                block = None;
                continue;
            }
            ":end" if block.is_some() => block.take().unwrap(),
            "" if block.is_none() => continue,
            _ => {
                if let Some(source) = &mut block {
                    source.push_str(&line);
                    continue;
                }
                line
            }
        };
        let mut vm = Vm::load(&source);
        vm.run();
        failed |= vm.status() != Status::Finished;
        if let Err(error) = emit_state(&vm) {
            eprintln!("cannot write REPL result: {error}");
            return ExitCode::from(2);
        }
    }
    if failed {
        ExitCode::FAILURE
    } else {
        ExitCode::SUCCESS
    }
}

type Snapshot = BTreeMap<PathBuf, Option<Vec<u8>>>;

fn snapshot(paths: impl IntoIterator<Item = PathBuf>) -> Snapshot {
    paths
        .into_iter()
        .map(|path| {
            let contents = std::fs::read(&path).ok();
            (path, contents)
        })
        .collect()
}

/// Poll contents (not timestamps), rerunning only when source or a linked dependency changes.
/// The optional run count makes the normal watch lifecycle usable in scripts and tests.
pub fn watch(path: &str, interval_ms: u64, iterations: Option<usize>) -> ExitCode {
    if path == "-" || interval_ms == 0 || iterations == Some(0) {
        eprintln!("watch requires a file, positive --interval-ms and positive --iterations");
        return ExitCode::from(2);
    }
    let path = Path::new(path);
    let mut previous: Option<Snapshot> = None;
    let mut runs = 0;
    loop {
        if let Some(old) = &previous {
            if snapshot(old.keys().cloned()) == *old {
                std::thread::sleep(Duration::from_millis(interval_ms));
                continue;
            }
        }
        let source = std::fs::read_to_string(path);
        let dependencies = source.as_ref().map_or_else(
            |_| vec![path.to_path_buf()],
            |source| crate::link::dependencies(source, path),
        );
        let mut observed = snapshot(dependencies);
        // Keep the root bytes actually evaluated: a save between read and snapshot must
        // trigger another run, rather than silently recording an unevaluated version.
        observed.insert(
            path.to_path_buf(),
            source.as_ref().ok().map(|text| text.as_bytes().to_vec()),
        );
        previous = Some(observed);
        let status = match source {
            Ok(source) => {
                let mut vm = Vm::load_file(&source, path);
                vm.run();
                if let Err(error) = emit_state(&vm) {
                    eprintln!("cannot write watch result: {error}");
                    return ExitCode::from(2);
                }
                if vm.status() == Status::Finished {
                    ExitCode::SUCCESS
                } else {
                    ExitCode::FAILURE
                }
            }
            Err(error) => {
                eprintln!("cannot read {}: {error}", path.display());
                ExitCode::from(2)
            }
        };
        runs += 1;
        if iterations.is_some_and(|limit| runs >= limit) {
            return status;
        }
        std::thread::sleep(Duration::from_millis(interval_ms));
    }
}

/// Emit shell-native completion scripts; shells remain responsible for filesystem completion.
pub fn completion(shell: &str) -> ExitCode {
    let script = match shell {
        "bash" => include_str!("../completions/helix.bash"),
        "zsh" => include_str!("../completions/_helix"),
        "fish" => include_str!("../completions/helix.fish"),
        _ => {
            eprintln!("completion requires bash, zsh, or fish");
            return ExitCode::from(2);
        }
    };
    print!("{}", script.replace("@COMMANDS@", &COMMANDS.join(" ")));
    ExitCode::SUCCESS
}
