use std::io::Read;
use std::process::ExitCode;

use helix::{Status, Vm};

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let [cmd, path] = args.as_slice() else {
        eprintln!("usage: helix run <file.yaml | ->");
        return ExitCode::from(2);
    };
    if cmd != "run" {
        eprintln!("unknown command `{cmd}`; usage: helix run <file.yaml | ->");
        return ExitCode::from(2);
    }
    let mut src = String::new();
    let read = if path == "-" { std::io::stdin().read_to_string(&mut src).map(drop) } else { std::fs::read_to_string(path).map(|s| src = s) };
    if let Err(e) = read {
        eprintln!("cannot read {path}: {e}");
        return ExitCode::from(2);
    }
    let mut vm = Vm::load(&src);
    vm.run();
    print!("{}", vm.state_string());
    if vm.status() == Status::Finished { ExitCode::SUCCESS } else { ExitCode::FAILURE }
}
