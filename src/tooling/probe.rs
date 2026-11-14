//! Explicit probes are the sole search stage that executes selected candidates.
use super::source::json;
use crate::{Status, Vm, value::Value};
use serde_json::{Value as J, json};
use std::{
    io::{self, Write},
    path::{Path, PathBuf},
};
use yaml_rust2::Yaml;

pub fn run(args: &[String]) -> Result<bool, (u8, String)> {
    let (mut cases, mut harness, mut roots) = (None, None, Vec::new());
    let mut max_steps = 2_000_000usize;
    let mut iter = args.iter();
    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "--cases" => cases = Some(iter.next().ok_or((2, "--cases requires a file".into()))?),
            "--harness" => {
                harness = Some(iter.next().ok_or((2, "--harness requires a file".into()))?)
            }
            "--max-steps" => {
                max_steps = iter
                    .next()
                    .ok_or((2, "--max-steps requires a positive integer".into()))?
                    .parse()
                    .map_err(|_| (2, "--max-steps requires a positive integer".into()))?;
                if max_steps == 0 {
                    return Err((2, "--max-steps must be positive".into()));
                }
            }
            "--root" => roots.push(PathBuf::from(
                iter.next().ok_or((2, "--root requires a path".into()))?,
            )),
            _ => return Err((2, format!("unknown probe option {arg}"))),
        }
    }
    let cases =
        super::stream::read(cases.ok_or((2, "--cases is required".into()))?).map_err(|e| (2, e))?;
    let cases = super::stream::data(&cases).map_err(|e| (2, e))?;
    let cases = cases.as_vec().ok_or((2, "cases must be a list".into()))?;
    for case in cases {
        if case["args"].as_vec().is_none() {
            return Err((2, "each case requires an args list".into()));
        }
    }
    let harness = harness
        .map(|p| super::stream::read(p).map(|s| (p, s)))
        .transpose()
        .map_err(|e| (2, e.to_string()))?;
    let mut out = io::stdout().lock();
    let mut input = io::stdin().lock();
    while let Some((candidate, _)) = super::stream::record(&mut input).map_err(|e| (1, e))? {
        if candidate["kind"] != "function" {
            serde_json::to_writer(&mut out, &candidate).map_err(|e| (1, e.to_string()))?;
            writeln!(out).map_err(|e| (1, e.to_string()))?;
            out.flush().map_err(|e| (1, e.to_string()))?;
            continue;
        }
        for (index, case) in cases.iter().enumerate() {
            let mut record = json!({"version":1,"kind":"probe","basis":"observed_example","candidate":candidate["reference"],"source":candidate["source"],"location":candidate["location"],"example":index,"input":json(case)});
            match example(
                &candidate,
                case,
                &roots,
                max_steps,
                harness.as_ref().map(|(p, s)| (Path::new(p), s.as_str())),
            ) {
                Ok((status, result, revision)) => {
                    record["status"] = json!(status);
                    record["result"] = result;
                    record["revision"] = json!(revision);
                }
                Err((status, detail)) => {
                    record["status"] = json!(status);
                    record["detail"] = json!(detail);
                }
            }
            serde_json::to_writer(&mut out, &record).map_err(|e| (1, e.to_string()))?;
            writeln!(out).map_err(|e| (1, e.to_string()))?;
            out.flush().map_err(|e| (1, e.to_string()))?;
        }
    }
    Ok(true)
}
fn drive(vm: &mut Vm, remaining: &mut usize) -> Result<(), (&'static str, String)> {
    vm.run_budgeted(remaining);
    if *remaining == 0 && matches!(vm.status(), Status::Ready | Status::Running) {
        return Err(("exhausted_budget", vm.state_string()));
    }
    if vm.pending_effect().is_some() {
        return Err(("attempted_effect", vm.state_string()));
    }
    if vm.status() != Status::Finished {
        let state = vm.state_string();
        return Err((
            if vm
                .error
                .as_ref()
                .is_some_and(|e| matches!(e.kind.as_str(), "limit" | "depth"))
            {
                "exhausted_budget"
            } else {
                "execution_error"
            },
            state,
        ));
    }
    Ok(())
}
fn call_value(value: Value, args: Vec<Value>) -> Result<Vm, (&'static str, String)> {
    let Value::Function(function) = value else {
        return Err((
            "unsupported_target",
            "selected export is not a function".into(),
        ));
    };
    let mut vm = Vm::from_function(function.clone());
    vm.evaluator = Some(std::sync::Arc::new(crate::eval::Evaluator::invoke(
        function, args,
    )));
    Ok(vm)
}
fn example(
    candidate: &J,
    case: &Yaml,
    roots: &[PathBuf],
    max_steps: usize,
    harness: Option<(&Path, &str)>,
) -> Result<(&'static str, J, String), (&'static str, String)> {
    let source = candidate["source"]
        .as_str()
        .ok_or(("unsupported_target", "missing source".into()))?;
    let standard = source.strip_prefix("std:");
    let text = if let Some(name) = standard {
        crate::std_source(name)
            .map(str::to_owned)
            .ok_or(("unsupported_target", "unknown module".into()))?
    } else {
        super::stream::read(source).map_err(|e| ("execution_error", e))?
    };
    if candidate["revision"].as_str() != Some(text.as_str()) {
        return Err((
            "source_changed",
            "indexed source differs; run a fresh search".into(),
        ));
    }
    if candidate["reference"]["path"].as_array().is_none() {
        return Err(("unsupported_target", "missing typed path".into()));
    }
    let mut remaining = max_steps;
    let owned;
    let program = if let Some(name) = standard {
        owned = format!("{{$call: [import, 'std:{name}']}}");
        owned.as_str()
    } else {
        text.as_str()
    };
    let mut setup = Vm::load_sandboxed(
        program,
        standard.is_none().then(|| Path::new(source)),
        roots,
    );
    drive(&mut setup, &mut remaining)?;
    // Select actual exported values, preserving lexical captures through result wrappers.
    // A source definition instantiated several times is ambiguous without an adapter.
    let mut pending = vec![
        setup
            .result
            .clone()
            .ok_or(("execution_error", "no module result".into()))?,
    ];
    let mut selected = None;
    let mut visited = 0;
    while let Some(value) = pending.pop() {
        visited += 1;
        if visited > crate::model::MAX_NODES {
            return Err((
                "exhausted_budget",
                "export traversal exceeds node bound".into(),
            ));
        }
        match value {
            Value::Function(function)
                if super::source::reference(&function.program.graph, function.node, source)
                    == candidate["reference"] =>
            {
                if selected.is_some() {
                    return Err((
                        "unsupported_target",
                        "definition has multiple exported instances".into(),
                    ));
                }
                selected = Some(Value::Function(function));
            }
            Value::Hash(fields) => pending.extend(fields.values().cloned()),
            Value::Array(items) => pending.extend(items.iter().cloned()),
            _ => {}
        }
    }
    let value = selected.ok_or((
        "unsupported_target",
        "definition is not present in the module's exported result".into(),
    ))?;
    let args = case["args"]
        .as_vec()
        .unwrap()
        .iter()
        .map(crate::value::from_host_yaml)
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| ("execution_error", e))?;
    let mut vm = if let Some((file, source)) = harness {
        let mut h = Vm::load_sandboxed(source, Some(file), roots);
        drive(&mut h, &mut remaining)?;
        let mut adapter = call_value(
            h.result.clone().unwrap(),
            vec![value, Value::Array(args.into())],
        )?;
        adapter.source_file = Some(file.to_path_buf());
        drive(&mut adapter, &mut remaining)?;
        call_value(adapter.result.clone().unwrap(), vec![])?
    } else {
        call_value(value, args)?
    };
    vm.source_file = standard.is_none().then(|| PathBuf::from(source));
    drive(&mut vm, &mut remaining)?;
    let value = vm.result.as_ref().unwrap();
    let ordinary = value.plain_yaml().ok_or((
        "unsupported_result",
        "probe result is not ordinary data".into(),
    ))?;
    let expected = case
        .as_hash()
        .and_then(|m| m.get(&Yaml::String("expected".into())));
    let status = if let Some(expected) = expected {
        if *value == crate::value::from_host_yaml(expected).map_err(|e| ("execution_error", e))? {
            "matched"
        } else {
            "mismatch"
        }
    } else {
        "observed"
    };
    Ok((status, json(&ordinary), text))
}
