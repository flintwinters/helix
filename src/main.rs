use std::process::ExitCode;

use helix::{Status, Vm};

const USAGE: &str = "usage: helix run <file.yaml | ->";

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let [cmd, path] = args.as_slice() else {
        eprintln!("{USAGE}");
        return ExitCode::from(2);
    };
    if cmd != "run" {
        eprintln!("unknown command `{cmd}`; {USAGE}");
        return ExitCode::from(2);
    }
    let read = if path == "-" { std::io::read_to_string(std::io::stdin()) } else { std::fs::read_to_string(path) };
    let src = match read {
        Ok(src) => src,
        Err(e) => {
            eprintln!("cannot read {path}: {e}");
            return ExitCode::from(2);
        }
    };
    let mut vm = Vm::load(&src);
    vm.run();
    print!("{}", vm.state_string());
    if vm.status() == Status::Finished { ExitCode::SUCCESS } else { ExitCode::FAILURE }
}
