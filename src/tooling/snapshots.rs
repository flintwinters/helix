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
            if vm.status() == Status::Error
                || (steps.is_none() && vm.status() != Status::Finished)
            {
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

/// Record an independently observed outcome; no effect is performed here.
pub fn reconcile(path: &str, outcome_path: &str) -> ExitCode {
    let result = (|| -> Result<(), String> {
        let text = std::fs::read_to_string(outcome_path).map_err(|e| e.to_string())?;
        let graph = crate::graph::Graph::parse_data(&text).map_err(|e| e.message)?;
        let data = graph
            .data(0)
            .plain_yaml()
            .ok_or("outcome must be ordinary data")?;
        let request = &data["request"];
        let expected = crate::EffectRequest {
            id: request["id"]
                .as_i64()
                .filter(|id| *id > 0)
                .ok_or("expected positive request id")? as u64,
            operation: request["operation"]
                .as_str()
                .ok_or("expected request operation")?
                .into(),
            payload: request["payload"].clone(),
        };
        if matches!(expected.payload, yaml_rust2::Yaml::BadValue) {
            return Err("missing request payload".into());
        }
        let outcome = &data["outcome"];
        if outcome.as_hash().is_none_or(|h| h.len() != 1) {
            return Err("outcome requires exactly one ok or error field".into());
        }
        let reply = if !matches!(outcome["ok"], yaml_rust2::Yaml::BadValue) {
            Ok(outcome["ok"].clone())
        } else {
            Err((
                outcome["error"]["kind"]
                    .as_str()
                    .ok_or("expected error kind")?
                    .into(),
                outcome["error"]["message"]
                    .as_str()
                    .ok_or("expected error message")?
                    .into(),
            ))
        };
        crate::vm::effects::durable::Checkpoint::reconcile(Path::new(path), &expected, reply)
    })();
    match result {
        Ok(()) => {
            println!("{}", serde_json::json!({"status":"reconciled"}));
            ExitCode::SUCCESS
        }
        Err(e) => error(&e),
    }
}
