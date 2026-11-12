use std::path::Path;
use std::process::ExitCode;

use helix::{Status, Vm};

const USAGE: &str = "usage: helix run <file.yaml | ->\n       helix check <file.yaml | ->    type phase: report every error, run nothing further\n       helix std [module]    list the standard modules, or print one's source";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.iter().map(String::as_str).collect::<Vec<_>>().as_slice() {
        ["run", path] => run(path, false),
        ["check", path] => run(path, true),
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
                eprintln!("unknown standard module `{name}`; available: {}", helix::std_modules().collect::<Vec<_>>().join(", "));
                ExitCode::from(2)
            }
        },
        [cmd, ..] if !matches!(*cmd, "run" | "check" | "std") => {
            eprintln!("unknown command `{cmd}`; {USAGE}");
            ExitCode::from(2)
        }
        _ => {
            eprintln!("{USAGE}");
            ExitCode::from(2)
        }
    }
}

fn run(path: &str, check: bool) -> ExitCode {
    let read = if path == "-" { std::io::read_to_string(std::io::stdin()) } else { std::fs::read_to_string(path) };
    let src = match read {
        Ok(src) => src,
        Err(e) => {
            eprintln!("cannot read {path}: {e}");
            return ExitCode::from(2);
        }
    };
    let mut vm = if path == "-" { Vm::load(&src) } else { Vm::load_file(&src, Path::new(path)) };
    if check { vm.check() } else { vm.run() }
    print!("{}", vm.state_string());
    if vm.status() == Status::Finished { ExitCode::SUCCESS } else { ExitCode::FAILURE }
}
