//! CLI formation and restoration of one coordinated promised system.
use crate::{PromisedSystem, Vm, graph::Graph};
use std::{collections::BTreeMap, io, path::Path, process::ExitCode};
use yaml_rust2::Yaml;

const MAX_STEPS: usize = 2_000_000;

fn load(input: &str, path: &str) -> Result<PromisedSystem, String> {
    let graph = Graph::parse_data(input).map_err(|error| error.message)?;
    let data = graph.data(0).plain_yaml().ok_or("system manifest must be ordinary data")?;
    if data.as_hash().is_none_or(|fields| fields.len() != 1) {
        return Err("system manifest requires exactly participants".into());
    }
    let participants = data["participants"].as_hash().ok_or("participants must map names to source strings")?;
    let promise = Yaml::Hash([
        (Yaml::String("type".into()), Yaml::String("promise".into())),
        (Yaml::String("name".into()), Yaml::String("reversible".into())),
        (Yaml::String("requires".into()), Yaml::Array(vec![Yaml::String("reversible".into())])),
    ].into_iter().collect());
    let mut machines = BTreeMap::new();
    for (name, source) in participants {
        let name = name.as_str().ok_or("participant names must be strings")?;
        let source = source.as_str().ok_or("participant sources must be strings")?;
        let machine = if path == "-" { Vm::load(source) } else { Vm::load_file(source, Path::new(path)) };
        machines.insert(name.to_owned(), machine.with_promise(promise.clone())?);
    }
    PromisedSystem::new(machines)
}

pub fn command(path: &str, restoring: bool, steps: Option<usize>) -> ExitCode {
    let result = (|| -> Result<(PromisedSystem, bool), String> {
        let count = steps.unwrap_or(MAX_STEPS);
        if count > MAX_STEPS { return Err("system transition count exceeds execution budget".into()); }
        let input = if path == "-" { io::read_to_string(io::stdin()) } else { std::fs::read_to_string(path) }
            .map_err(|error| error.to_string())?;
        let mut system = if restoring { PromisedSystem::restore(&input)? } else { load(&input, path)? };
        let mut stopped = false;
        for _ in 0..count {
            match system.advance()? {
                Some(next) => system = next,
                None => { stopped = true; break; }
            }
        }
        Ok((system, stopped))
    })();
    match result {
        Ok((system, stopped)) => {
            if steps.is_some() {
                match system.snapshot() {
                    Ok(snapshot) => {
                        print!("{snapshot}");
                        if system.state()["status"].as_str() == Some("error") { ExitCode::FAILURE }
                        else { ExitCode::SUCCESS }
                    }
                    Err(error) => failure(&error),
                }
            } else {
                let mut state = system.state();
                if !stopped && !system.finished() {
                    if let Yaml::Hash(fields) = &mut state {
                        fields.insert(Yaml::String("status".into()), Yaml::String("budget_exhausted".into()));
                    }
                }
                print!("{}", crate::yaml_document(&state));
                if system.finished() { ExitCode::SUCCESS } else { ExitCode::FAILURE }
            }
        }
        Err(error) => failure(&error),
    }
}
fn failure(message: &str) -> ExitCode {
    println!("{}", serde_json::json!({"status":"error","errors":[{"kind":"promised_system","message":message}]}));
    ExitCode::FAILURE
}
