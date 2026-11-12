//! Pipeline interface: source in, evaluated value out.
use std::process::ExitCode;

use helix::{Status, Vm, yaml_document};
use yaml_rust2::Yaml;

const USAGE: &str = "usage: hx [PROGRAM | -]\nEvaluate inline YAML, or read a program from stdin.\nStrings are emitted raw; other values are emitted as YAML.\nFailures emit VM state to stderr and leave stdout empty.";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let source = match args.as_slice() {
        [flag] if matches!(flag.as_str(), "--help" | "-h") => {
            println!("{USAGE}");
            return ExitCode::SUCCESS;
        }
        [] => None,
        [source] if source == "-" => None,
        [source] => Some(source.clone()),
        _ => {
            eprintln!("{USAGE}");
            return ExitCode::from(2);
        }
    };
    let source = match source.map(Ok).unwrap_or_else(|| std::io::read_to_string(std::io::stdin())) {
        Ok(source) => source,
        Err(error) => {
            eprintln!("cannot read stdin: {error}");
            return ExitCode::from(2);
        }
    };
    let mut vm = Vm::load(&source);
    vm.run();
    if vm.status() != Status::Finished {
        eprint!("{}", vm.state_string());
        return ExitCode::FAILURE;
    }
    let state = vm.state();
    let value = &state["root"];
    match value {
        Yaml::String(text) => print!("{text}"),
        value => print!("{}", yaml_document(value)),
    }
    ExitCode::SUCCESS
}
