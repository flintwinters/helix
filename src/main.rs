use std::path::Path;
use std::process::ExitCode;

use helix::{Status, Vm, json_document, yaml_document};
use yaml_rust2::Yaml;

const USAGE: &str = "usage: helix exec [--json | --yaml] [PROGRAM | -]    inline source or stdin; default raw strings, otherwise YAML\n       helix run <file.yaml | ->    emit full VM state\n       helix sandbox <root | -> <file.yaml | ->    restrict transitive file imports\n       helix recover <file.yaml | ->    save a pure recovery checkpoint on failure\n       helix resume <state.yaml | ->    run edited recovery source\n       helix check <file.yaml | ->    collect independent evaluation failures\n       helix std [module]    list the standard modules, or print one's source\n       helix --help\n\nexec failures emit VM state to stderr, leave stdout empty, and exit 1.\nUse exec -- PROGRAM to escape reserved arguments.";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args
        .iter()
        .map(String::as_str)
        .collect::<Vec<_>>()
        .as_slice()
    {
        ["--help" | "-h"] | ["exec", "--help" | "-h"] => {
            println!("{USAGE}");
            ExitCode::SUCCESS
        }
        ["exec", format] if matches!(*format, "--json" | "--yaml") => {
            execute("-", "exec", None, format)
        }
        ["exec", format, "--", source] if matches!(*format, "--json" | "--yaml") => {
            execute(source, "exec", None, format)
        }
        ["exec", format, source]
            if matches!(*format, "--json" | "--yaml") && !source.starts_with("--") =>
        {
            execute(source, "exec", None, format)
        }
        ["exec"] => run("-", "exec"),
        ["exec", "--", source] => run(source, "exec"),
        ["exec", source] if !source.starts_with("--") => run(source, "exec"),
        ["run", path] => run(path, "run"),
        ["recover", path] => run(path, "recover"),
        ["resume", path] => run(path, "resume"),
        ["check", path] => run(path, "check"),
        ["sandbox", root, path] => execute(path, "run", Some(root), "raw"),
        ["std"] => {
            helix::std_modules().for_each(|name| println!("std:{name}"));
            ExitCode::SUCCESS
        }
        ["std", name] => match helix::std_source(name.trim_start_matches("std:")) {
            Some(source) => {
                print!("{source}");
                ExitCode::SUCCESS
            }
            None => {
                eprintln!(
                    "unknown standard module `{name}`; available: {}",
                    helix::std_modules().collect::<Vec<_>>().join(", ")
                );
                ExitCode::from(2)
            }
        },
        [cmd, ..]
            if !matches!(
                *cmd,
                "exec" | "run" | "check" | "recover" | "resume" | "sandbox" | "std"
            ) =>
        {
            eprintln!("unknown command `{cmd}`; {USAGE}");
            ExitCode::from(2)
        }
        _ => {
            eprintln!("{USAGE}");
            ExitCode::from(2)
        }
    }
}

fn run(path: &str, mode: &str) -> ExitCode {
    execute(path, mode, None, "raw")
}

fn execute(path: &str, mode: &str, allowed_root: Option<&str>, format: &str) -> ExitCode {
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
        } else {
            vm.state_string()
        }
    );
    if vm.status() == Status::Finished {
        ExitCode::SUCCESS
    } else {
        ExitCode::FAILURE
    }
}
