//! Incremental ordinary-data invocation of a linked query.
use std::{io::{self, BufRead, Write}, path::Path, process::ExitCode};
use crate::{Vm, Status, json_document, yaml_document};
use yaml_rust2::{Yaml, YamlLoader};

fn data(text: &str) -> Result<Yaml, String> {
    let mut docs = YamlLoader::load_from_str(text).map_err(|e| e.to_string())?;
    if docs.len() != 1 { return Err("expected one data document".into()); }
    Ok(docs.remove(0))
}
fn read(path: &str) -> Result<String, String> {
    (if path == "-" { io::read_to_string(io::stdin()) } else { std::fs::read_to_string(path) }).map_err(|e| e.to_string())
}
pub fn command(args: &[String]) -> ExitCode {
    match execute(args) { Ok(()) => ExitCode::SUCCESS, Err((code, message)) => { eprintln!("{message}"); ExitCode::from(code) } }
}
fn execute(args: &[String]) -> Result<(), (u8, String)> {
    let mut stream = false;
    let (mut input, mut file, mut source, mut format) = (None, None, None, "raw");
    let mut args = args.iter();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--stream" => stream = true,
            "--input" => input = Some(args.next().ok_or((2, "--input requires a path".into()))?.as_str()),
            "--file" => file = Some(args.next().ok_or((2, "--file requires a path".into()))?.as_str()),
            "--json" => format = "json",
            "--yaml" => format = "yaml",
            "--" => { source = args.next().map(String::as_str); if args.next().is_some() { return Err((2,"too many programs".into())); } break; }
            x if !x.starts_with("--") && source.is_none() => source = Some(x),
            _ => return Err((2, format!("unexpected argument {arg}"))),
        }
    }
    if file.is_some() && source.is_some() || stream && input.is_some() { return Err((2,"conflicting input options".into())); }
    if stream && (file == Some("-") || source == Some("-")) || input == Some("-") && (file == Some("-") || source == Some("-")) { return Err((2,"program and data cannot both consume stdin".into())); }
    let program = if let Some(path) = file { read(path) } else { source.ok_or("a program or --file is required".into()).and_then(|s| if s == "-" { read(s) } else { Ok(s.into()) }) }.map_err(|e| (2,e))?;
    if stream && (file == Some("-") || source == Some("-")) || input == Some("-") && (file == Some("-") || source == Some("-")) { return Err((2,"program and data cannot both consume stdin".into())); }
    let mut query = if let Some(path) = file { Vm::load_file(&program, Path::new(path)) } else { Vm::load(&program) };
    query.run();
    if query.status() != Status::Finished { return Err((1,query.state_string())); }
    let mut out = io::stdout().lock();
    let mut invoke = |value: Yaml| -> Result<(), (u8,String)> {
        let mut vm = query.call(&[value]).map_err(|e| (1,e))?;
        vm.run();
        if vm.status() != Status::Finished { return Err((1,vm.state_string())); }
        let state = vm.state();
        if state["objects"].as_hash().is_some() { return Err((1,"query must return ordinary data".into())); }
        let value = &state["root"];
        let records = if stream { value.as_vec().ok_or((1,"stream query must return a list".into()))?.clone() } else { vec![value.clone()] };
        for record in records {
            let rendered = if stream || format == "json" { json_document(&record).map_err(|e| (1,e.into()))? } else if format == "raw" { match record { Yaml::String(s) => s, _ => yaml_document(&record) } } else { yaml_document(&record) };
            out.write_all(rendered.as_bytes()).map_err(|e| (1,e.to_string()))?;
        }
        out.flush().map_err(|e| (1,e.to_string()))
    };
    if stream {
        for line in io::stdin().lock().lines() {
            let line = line.map_err(|e| (1,e.to_string()))?;
            // JSON syntax is checked independently; YAML's larger language is not JSONL.
            serde_json::from_str::<serde_json::Value>(&line).map_err(|e| (1,e.to_string()))?;
            invoke(data(&line).map_err(|e| (1,e))?)?;
        }
    } else { invoke(data(&read(input.unwrap_or("-")).map_err(|e| (2,e))?).map_err(|e| (2,e))?)?; }
    Ok(())
}
