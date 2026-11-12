//! Observational CLI views of live transitions and saved data; never a resume format.
use std::io::{self, Write};
use std::path::Path;
use std::process::ExitCode;

use crate::{
    model::{Status, Vm},
    graph::{Graph, ROOT},
    emit::document,
};
use yaml_rust2::Yaml;

fn text(value: &str) -> Yaml {
    Yaml::String(value.into())
}
fn map(entries: impl IntoIterator<Item = (&'static str, Yaml)>) -> Yaml {
    Yaml::Hash(
        entries
            .into_iter()
            .map(|(key, value)| (text(key), value))
            .collect(),
    )
}
fn field(value: &Yaml, key: &str) -> Yaml {
    match &value[key] {
        Yaml::BadValue => Yaml::Null,
        value => value.clone(),
    }
}
fn read(path: &str) -> io::Result<String> {
    if path == "-" {
        io::read_to_string(io::stdin())
    } else {
        std::fs::read_to_string(path)
    }
}
fn load(path: &str) -> Result<Vm, ExitCode> {
    read(path)
        .map(|source| {
            if path == "-" {
                Vm::load(&source)
            } else {
                Vm::load_file(&source, Path::new(path))
            }
        })
        .map_err(|error| {
            eprintln!("cannot read {path}: {error}");
            ExitCode::from(2)
        })
}
fn write(value: &Yaml, stream: bool) -> io::Result<()> {
    let stdout = io::stdout();
    let mut output = stdout.lock();
    if stream {
        writeln!(output, "---")?;
    }
    output.write_all(document(value).as_bytes())?;
    output.flush()
}
fn output_error(error: io::Error) -> ExitCode {
    eprintln!("cannot write inspection: {error}");
    ExitCode::from(2)
}
fn terminal(vm: &Vm) -> bool {
    !matches!(vm.status(), Status::Ready | Status::Running)
}
fn result(vm: &Vm) -> ExitCode {
    if vm.status() == Status::Error {
        ExitCode::FAILURE
    } else {
        ExitCode::SUCCESS
    }
}
fn snapshot(vm: &Vm, transition: usize) -> Yaml {
    let machine = vm.inspect();
    let state = vm.state();
    map([
        ("format", text("helix.inspection.v1")),
        ("transition", Yaml::Integer(transition as i64)),
        ("status", text(vm.status().as_str())),
        ("location", field(&machine, "location")),
        ("pending_effect", field(&state, "pending_effect")),
        ("restorable", Yaml::Boolean(false)),
        ("machine", machine),
        ("state", state),
    ])
}

/// Advance at most N transitions and emit one inspectable machine version.
pub fn step(path: &str, transitions: usize) -> ExitCode {
    let mut vm = match load(path) {
        Ok(vm) => vm,
        Err(code) => return code,
    };
    let mut count = 0;
    while count < transitions && !terminal(&vm) {
        vm = vm.step();
        count += 1;
    }
    if let Err(error) = write(&snapshot(&vm, count), false) {
        return output_error(error);
    }
    result(&vm)
}

/// Stream the initial version and each subsequent transition as separate YAML documents.
pub fn trace(path: &str, limit: Option<usize>) -> ExitCode {
    let mut vm = match load(path) {
        Ok(vm) => vm,
        Err(code) => return code,
    };
    let mut count = 0;
    loop {
        if let Err(error) = write(&snapshot(&vm, count), true) {
            return output_error(error);
        }
        if terminal(&vm) || limit.is_some_and(|limit| count >= limit) {
            return result(&vm);
        }
        vm = vm.step();
        count += 1;
    }
}

fn value_summary(value: &Yaml) -> Yaml {
    let (kind, size) = match value {
        Yaml::Hash(values) => ("map", Some(values.len())),
        Yaml::Array(values) => ("list", Some(values.len())),
        Yaml::String(value) => ("string", Some(value.chars().count())),
        Yaml::Integer(_) => ("integer", None),
        Yaml::Real(_) => ("float", None),
        Yaml::Boolean(_) => ("boolean", None),
        Yaml::Null => ("null", None),
        _ => ("absent", None),
    };
    map([
        ("kind", text(kind)),
        (
            "size",
            size.map(|n| Yaml::Integer(n as i64)).unwrap_or(Yaml::Null),
        ),
    ])
}

/// Inspect exactly one state/inspection document as ordinary data, without linking or evaluation.
pub fn inspect(path: &str) -> ExitCode {
    let source = match read(path) {
        Ok(source) => source,
        Err(error) => {
            eprintln!("cannot read {path}: {error}");
            return ExitCode::from(2);
        }
    };
    let document = match Graph::parse_data(&source) {
        Ok(graph) => graph
            .data(ROOT)
            .plain_yaml()
            .expect("parsed data is ordinary YAML"),
        Err(error) => {
            let report = map([
                ("status", text("error")),
                (
                    "error",
                    error
                        .to_value()
                        .plain_yaml()
                        .expect("parse errors are data"),
                ),
            ]);
            return match write(&report, false) {
                Ok(()) => ExitCode::from(2),
                Err(error) => output_error(error),
            };
        }
    };
    let Some(status @ ("ready" | "running" | "finished" | "error" | "signaled")) =
        document["status"].as_str()
    else {
        eprintln!("expected a state or inspection map with a valid status");
        return ExitCode::from(2);
    };
    let state = if document["state"].as_hash().is_some() {
        &document["state"]
    } else {
        &document
    };
    let machine = if document["machine"].as_hash().is_some() {
        &document["machine"]
    } else {
        &document
    };
    let location = if machine["location"] != Yaml::BadValue {
        field(machine, "location")
    } else if state["error"]["reference"] != Yaml::BadValue {
        field(&state["error"], "reference")
    } else {
        field(&state["error"], "path")
    };
    let summary = map([
        ("format", text("helix.state-summary.v1")),
        ("status", text(status)),
        ("location", location),
        (
            "pending_effect",
            if state["pending_effect"] != Yaml::BadValue {
                field(state, "pending_effect")
            } else {
                field(machine, "pending_effect")
            },
        ),
        ("root", value_summary(&state["root"])),
        ("error", field(state, "error")),
        ("errors", field(state, "errors")),
        (
            "continuations",
            Yaml::Integer(machine["continuations"].as_vec().map_or(0, Vec::len) as i64),
        ),
        (
            "objects",
            Yaml::Integer(
                machine["objects"]
                    .as_hash()
                    .map_or(0, |values| values.len()) as i64,
            ),
        ),
        (
            "recovery_available",
            Yaml::Boolean(state["recovery"].as_str().is_some()),
        ),
        ("transition", field(&document, "transition")),
    ]);
    match write(&summary, false) {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => output_error(error),
    }
}
