//! Incremental ordinary-data invocation of a linked query.
use crate::{Status, Vm, json_document, yaml_document};
use std::{
    io::{self, BufRead, Read, Write},
    path::Path,
    process::ExitCode,
};
use yaml_rust2::Yaml;

pub(crate) const MAX_INPUT_BYTES: u64 = 16_000_000;
pub(crate) fn data(text: &str) -> Result<Yaml, String> {
    let graph = crate::graph::Graph::parse_data(text).map_err(|e| e.message)?;
    Ok(graph.data(0).plain_yaml().unwrap())
}
/// Read one bounded, explicitly framed YAML document or a legacy JSON line.
pub(crate) fn record(
    reader: &mut impl BufRead,
) -> Result<Option<Yaml>, String> {
    let mut text = String::new();
    if read_record_line(reader, &mut text)? == 0 {
        return Ok(None);
    }
    let value = if text.trim_end() == "---" {
        loop {
            let start = text.len();
            if read_record_line(reader, &mut text)? == 0 {
                return Err("unterminated YAML stream document; end it with `...`".into());
            }
            if text[start..].trim_end() == "..." {
                break data(&text)?;
            }
        }
    } else {
        serde_json::from_str::<serde_json::Value>(&text)
            .map_err(|error| format!("expected a YAML document starting with `---`: {error}"))?;
        data(&text)?
    };
    Ok(Some(value))
}

fn read_record_line(reader: &mut impl BufRead, text: &mut String) -> Result<usize, String> {
    let size = std::io::Read::take(reader, MAX_INPUT_BYTES + 1 - text.len() as u64)
        .read_line(text)
        .map_err(|error| error.to_string())?;
    if text.len() as u64 > MAX_INPUT_BYTES {
        return Err("stream document exceeds 16000000 bytes".into());
    }
    Ok(size)
}

/// Frame each response so a consumer can finish reading it before the next request.
pub(crate) fn write_record(out: &mut impl Write, value: &Yaml) -> io::Result<()> {
    writeln!(out, "---")?;
    out.write_all(yaml_document(value).as_bytes())?;
    writeln!(out, "...")?;
    out.flush()
}
pub(crate) fn read(path: &str) -> Result<String, String> {
    read_bounded(path, MAX_INPUT_BYTES)
}
pub(crate) fn read_bounded(path: &str, limit: u64) -> Result<String, String> {
    let input: Box<dyn std::io::Read> = if path == "-" {
        Box::new(io::stdin())
    } else {
        Box::new(std::fs::File::open(path).map_err(|e| e.to_string())?)
    };
    let mut text = String::new();
    input
        .take(limit + 1)
        .read_to_string(&mut text)
        .map_err(|e| e.to_string())?;
    if text.len() as u64 > limit {
        return Err(format!("input exceeds {limit} bytes"));
    }
    Ok(text)
}
pub fn command(args: &[String]) -> ExitCode {
    match execute(args) {
        Ok(()) => ExitCode::SUCCESS,
        Err((code, message)) => {
            eprintln!("{message}");
            ExitCode::from(code)
        }
    }
}
fn execute(args: &[String]) -> Result<(), (u8, String)> {
    let mut stream = false;
    let (mut input, mut file, mut source, mut format) = (None, None, None, "raw");
    let mut args = args.iter();
    while let Some(arg) = args.next() {
        match arg.as_str() {
            "--stream" => stream = true,
            "--input" => {
                input = Some(
                    args.next()
                        .ok_or((2, "--input requires a path".into()))?
                        .as_str(),
                )
            }
            "--file" => {
                file = Some(
                    args.next()
                        .ok_or((2, "--file requires a path".into()))?
                        .as_str(),
                )
            }
            "--json" => format = "json",
            "--yaml" => format = "yaml",
            "--" => {
                source = args.next().map(String::as_str);
                if args.next().is_some() {
                    return Err((2, "too many programs".into()));
                }
                break;
            }
            x if !x.starts_with("--") && source.is_none() => source = Some(x),
            _ => return Err((2, format!("unexpected argument {arg}"))),
        }
    }
    if file.is_some() && source.is_some() || stream && input.is_some() {
        return Err((2, "conflicting input options".into()));
    }
    let program = if let Some(path) = file {
        read(path)
    } else {
        source
            .ok_or("a program or --file is required".into())
            .and_then(|s| if s == "-" { read(s) } else { Ok(s.into()) })
    }
    .map_err(|e| (2, e))?;
    if stream && (file == Some("-") || source == Some("-"))
        || input == Some("-") && (file == Some("-") || source == Some("-"))
    {
        return Err((2, "program and data cannot both consume stdin".into()));
    }
    let mut query = if let Some(path) = file {
        Vm::load_file(&program, Path::new(path))
    } else {
        Vm::load(&program)
    };
    query.run();
    if query.status() != Status::Finished {
        return Err((1, query.state_string()));
    }
    let mut out = io::stdout().lock();
    let mut invoke = |value: Yaml| -> Result<(), (u8, String)> {
        let mut vm = query.call(&[value]).map_err(|e| (1, e))?;
        vm.run();
        if vm.status() != Status::Finished {
            return Err((1, vm.state_string()));
        }
        let state = vm.state();
        if state["objects"].as_hash().is_some() {
            return Err((1, "query must return ordinary data".into()));
        }
        let value = &state["root"];
        let records = if stream {
            value
                .as_vec()
                .ok_or((1, "stream query must return a list".into()))?
                .clone()
        } else {
            vec![value.clone()]
        };
        for record in records {
            if stream && format != "json" {
                write_record(&mut out, &record).map_err(|error| (1, error.to_string()))?;
                continue;
            }
            let rendered = if format == "json" {
                json_document(&record).map_err(|e| (1, e.into()))?
            } else if format == "raw" {
                match record {
                    Yaml::String(s) => s,
                    _ => yaml_document(&record),
                }
            } else {
                yaml_document(&record)
            };
            out.write_all(rendered.as_bytes())
                .map_err(|e| (1, e.to_string()))?;
        }
        out.flush().map_err(|e| (1, e.to_string()))
    };
    if stream {
        let mut input = io::stdin().lock();
        while let Some(value) = record(&mut input).map_err(|e| (1, e))? {
            invoke(value)?;
        }
    } else {
        invoke(data(&read(input.unwrap_or("-")).map_err(|e| (2, e))?).map_err(|e| (2, e))?)?;
    }
    Ok(())
}
