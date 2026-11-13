//! Disk execution checkpoints. Ordinary recovery and inspection keep their own formats.
use crate::{Status, Vm};
use std::{io, path::Path, process::ExitCode};

pub fn command(path: &str, restoring: bool, steps: Option<usize>) -> ExitCode {
    let result = (|| -> Result<Vm, String> {
        let input = if path == "-" {
            io::read_to_string(io::stdin())
        } else {
            std::fs::read_to_string(path)
        }
        .map_err(|e| e.to_string())?;
        let mut vm = if restoring {
            Vm::restore(&input)?
        } else if path == "-" {
            Vm::load(&input)
        } else {
            Vm::load_file(&input, Path::new(path))
        };
        if let Some(steps) = steps {
            if steps > 2_000_000 {
                return Err("snapshot transition count exceeds execution budget".into());
            }
            for _ in 0..steps {
                if !matches!(vm.status(), Status::Ready | Status::Running) {
                    break;
                }
                vm = vm.step();
            }
        } else {
            vm.run();
        }
        Ok(vm)
    })();
    match result {
        Ok(vm) => {
            if steps.is_some() {
                match vm.snapshot() {
                    Ok(text) => print!("{text}"),
                    Err(e) => return error(&e),
                }
            } else {
                print!("{}", vm.state_string());
            }
            if vm.status() == Status::Error {
                ExitCode::FAILURE
            } else {
                ExitCode::SUCCESS
            }
        }
        Err(e) => error(&e),
    }
}
fn error(message: &str) -> ExitCode {
    println!(
        "{}",
        serde_json::json!({"status":"error","errors":[{"kind":"snapshot","message":message}]})
    );
    ExitCode::FAILURE
}
