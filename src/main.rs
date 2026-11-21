use std::path::Path;
use std::process::ExitCode;

use helix::tooling::help;
use helix::{Status, Vm, json_document, yaml_document};
use yaml_rust2::Yaml;


fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let argv: Vec<&str> = args.iter().map(String::as_str).collect();
    // Structured help comes from the one command table, before commands with their own prose help.
    if let [cmd, "--help" | "-h", "--json"] = argv.as_slice() && help::find(cmd).is_some() {
        return help::command(&argv);
    }
    if let Some(code) = helix::tooling::cli::dispatch(&args) {
        return code;
    }
    if args.first().is_some_and(|arg| arg == "perform") {
        return helix::tooling::perform::run(&args[1..]);
    }
    match argv.as_slice() {
        ["--help" | "-h" | "help", rest @ ..] => help::command(rest),
        [cmd, "--help" | "-h", rest @ ..] if help::find(cmd).is_some() => help::command(&[&[*cmd], rest].concat()),
        ["exec", format] if matches!(*format, "--json" | "--yaml") => {
            execute("-", "exec", None, format, false)
        }
        ["exec", format, "--", source] if matches!(*format, "--json" | "--yaml") => {
            execute(source, "exec", None, format, false)
        }
        ["exec", format, source]
            if matches!(*format, "--json" | "--yaml") && !source.starts_with("--") =>
        {
            execute(source, "exec", None, format, false)
        }
        ["exec"] => run("-", "exec"),
        ["exec", "--", source] => run(source, "exec"),
        ["exec", source] if !source.starts_with("--") => run(source, "exec"),
        ["run", "--annotations", path] => execute(path, "run", None, "raw", true),
        ["run", path] => run(path, "run"),
        ["recover", path] => run(path, "recover"),
        ["resume", path] => run(path, "resume"),
        ["check", "--evaluate", path] | ["check", path, "--evaluate"] => run(path, "check"),
        ["sandbox", root, path] => execute(path, "run", Some(root), "raw", false),
        ["std", rest @ ..] => helix::tooling::catalog::command(rest),
        ["docs", rest @ ..] => helix::tooling::docs::command(rest),
        ["find", rest @ ..] => helix::tooling::find::command(rest),
        ["builtins", rest @ ..] => helix::tooling::docs::builtins(rest),
        [cmd, ..] => match help::find(cmd) {
            None => help::unknown(cmd),
            Some(command) => {
                eprintln!("{}\n`helix --help` lists every command", command.usage());
                ExitCode::from(2)
            }
        },
        [] => {
            eprintln!("{}", help::overview());
            ExitCode::from(2)
        }
    }
}

fn run(path: &str, mode: &str) -> ExitCode {
    execute(path, mode, None, "raw", false)
}

fn execute(path: &str, mode: &str, allowed_root: Option<&str>, format: &str, annotations: bool) -> ExitCode {
    let read = if path == "-" {
        std::io::read_to_string(std::io::stdin())
    } else if mode == "exec" {
        Ok(path.to_owned())
    } else {
        std::fs::read_to_string(path)
    };
    let src = match read {
        Ok(src) => src,
        Err(e) => {
            eprintln!("cannot read {path}: {e}");
            return ExitCode::from(2);
        }
    };
    let mut vm = if let Some(root) = allowed_root {
        let roots = if root == "-" {
            Vec::new()
        } else {
            vec![root.into()]
        };
        Vm::load_sandboxed(&src, (path != "-").then(|| Path::new(path)), &roots)
    } else if mode == "resume" {
        Vm::resume(&src)
    } else if path == "-" || mode == "exec" {
        Vm::load(&src)
    } else {
        Vm::load_file(&src, Path::new(path))
    };
    if mode == "check" {
        vm.check()
    } else {
        vm.run()
    }
    if mode == "exec" {
        if vm.status() != Status::Finished {
            eprint!("{}", vm.state_string());
            return ExitCode::FAILURE;
        }
        let state = vm.state();
        let value = if state["objects"].as_hash().is_some() {
            Yaml::Hash(
                [
                    (Yaml::String("value".into()), state["root"].clone()),
                    (Yaml::String("objects".into()), state["objects"].clone()),
                ]
                .into_iter()
                .collect(),
            )
        } else {
            state["root"].clone()
        };
        if format == "--json" {
            match json_document(&value) {
                Ok(text) => print!("{text}"),
                Err(message) => {
                    eprintln!("cannot encode result as JSON: {message}");
                    return ExitCode::FAILURE;
                }
            }
            return ExitCode::SUCCESS;
        }
        match &value {
            Yaml::String(text) if format == "raw" => print!("{text}"),
            value => print!("{}", yaml_document(value)),
        }
        return ExitCode::SUCCESS;
    }
    print!(
        "{}",
        if matches!(mode, "recover" | "resume") {
            vm.recovery_state_string()
        } else if annotations || mode != "run" {
            vm.state_string()
        } else {
            let mut state = vm.state();
            omit_annotations(&mut state);
            yaml_document(&state)
        }
    );
    if vm.status() == Status::Finished {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}

/// Default `run` output drops evaluation annotations (the bulk of its size) and
/// reports how many were omitted; `Vm::state` keeps them for API consumers.
fn omit_annotations(state: &mut Yaml) {
    let Yaml::Hash(fields) = state else { return };
    let omitted = match fields.remove(&Yaml::String("annotations".into())) {
        Some(Yaml::Array(items)) => items.len(),
        _ => 0,
    };
    fields.remove(&Yaml::String("annotations_truncated".into()));
    if omitted > 0 {
        fields.insert(
            Yaml::String("annotations_omitted".into()),
            Yaml::Integer(omitted as i64),
        );
    }
}
