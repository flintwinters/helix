use std::process::ExitCode;

use crate::tooling::{analysis, inspection, interactive};

pub fn dispatch(args: &[String]) -> Option<ExitCode> {
    if args
        .get(1)
        .is_some_and(|arg| matches!(arg.as_str(), "--help" | "-h"))
    {
        return None;
    }
    let words: Vec<&str> = args.iter().map(String::as_str).collect();
    Some(match words.as_slice() {
        ["check", "--evaluate", _] | ["check", _, "--evaluate"] => return None,
        ["check", path] => source(path, false),
        ["fmt", path] => source(path, true),
        ["schema"] => {
            print!("{}", analysis::schema());
            ExitCode::SUCCESS
        }
        ["lsp"] => match analysis::lsp() {
            Ok(()) => ExitCode::SUCCESS,
            Err(error) => {
                eprintln!("LSP transport failed: {error}");
                ExitCode::from(2)
            }
        },
        ["repl"] => interactive::repl(),
        ["completion", shell] => interactive::completion(shell),
        ["inspect", path] => inspection::inspect(path),
        ["step", path] => inspection::step(path, 1),
        ["step", path, "--steps", count] => match count.parse() {
            Ok(count) => inspection::step(path, count),
            Err(_) => usage("--steps requires a nonnegative integer"),
        },
        ["trace", path] => inspection::trace(path, None),
        ["trace", path, "--limit", count] => match count.parse() {
            Ok(count) => inspection::trace(path, Some(count)),
            Err(_) => usage("--limit requires a nonnegative integer"),
        },
        ["watch", path, rest @ ..] => watch(path, rest),
        _ => return None,
    })
}

fn usage(message: &str) -> ExitCode {
    eprintln!("{message}; see helix --help");
    ExitCode::from(2)
}

fn source(path: &str, format: bool) -> ExitCode {
    let input = if path == "-" {
        std::io::read_to_string(std::io::stdin())
    } else {
        std::fs::read_to_string(path)
    };
    let input = match input {
        Ok(input) => input,
        Err(error) => {
            eprintln!("cannot read {path}: {error}");
            return ExitCode::from(2);
        }
    };
    if format {
        match analysis::format(&input) {
            Ok(output) => {
                print!("{output}");
                ExitCode::SUCCESS
            }
            Err(error) => {
                eprint!("{error}");
                ExitCode::FAILURE
            }
        }
    } else {
        let (output, valid) = analysis::check(&input);
        print!("{output}");
        if valid {
            ExitCode::SUCCESS
        } else {
            ExitCode::FAILURE
        }
    }
}

fn watch(path: &str, options: &[&str]) -> ExitCode {
    let mut interval = 250;
    let mut iterations = None;
    let mut seen = std::collections::HashSet::new();
    for pair in options.chunks(2) {
        let [flag, value] = pair else {
            return usage("watch options require values");
        };
        if !seen.insert(*flag) {
            return usage("duplicate watch option");
        }
        match *flag {
            "--interval-ms" => match value.parse::<u64>() {
                Ok(value) if value > 0 => interval = value,
                _ => return usage("--interval-ms requires a positive integer"),
            },
            "--iterations" => match value.parse::<usize>() {
                Ok(value) if value > 0 => iterations = Some(value),
                _ => return usage("--iterations requires a positive integer"),
            },
            _ => return usage("unknown watch option"),
        }
    }
    interactive::watch(path, interval, iterations)
}
