//! Aggregate observations over owned VM execution without retaining a transition stream.
use std::{
    collections::HashMap,
    io::{self, Write},
    process::ExitCode,
    sync::Arc,
    time::Instant,
};

use serde_json::{Value, json};

use crate::model::{Actor, Evaluator, NodeId, Operation, Program, Request, Status};

#[derive(Default)]
struct Cost {
    transitions: usize,
    elapsed_ns: u128,
}

impl Cost {
    fn record(&mut self, elapsed_ns: u128) {
        self.transitions += 1;
        self.elapsed_ns += elapsed_ns;
    }
}

struct Site {
    program: Arc<Program>,
    cost: Cost,
}

type Sites = HashMap<(usize, NodeId, &'static str), Site>;

fn observe(evaluator: &mut Evaluator, sites: &mut Sites, other: &mut Cost) {
    let site = match evaluator.requests.last() {
        Some(Request::Eval(node)) => Some((*node, "evaluate")),
        Some(Request::Apply(node, Actor::Builtin(_), ..)) => Some((*node, "apply_builtin")),
        Some(Request::Apply(node, Actor::Function(_), ..)) => Some((*node, "apply_function")),
        Some(Request::Call(node, ..)) => Some((*node, "call")),
        Some(Request::Atomic(operation)) => Some((
            operation.site,
            match operation.operation {
                Operation::Builtin(..) => "atomic_builtin",
                Operation::Violations(..) => "check_type",
            },
        )),
        _ => None,
    };
    let cost = match site {
        Some((node, phase)) => {
            let program = &evaluator.program;
            &mut sites
                .entry((Arc::as_ptr(program) as usize, node, phase))
                .or_insert_with(|| Site {
                    program: program.clone(),
                    cost: Cost::default(),
                })
                .cost
        }
        None => other,
    };
    let started = Instant::now();
    evaluator.transition();
    cost.record(started.elapsed().as_nanos());
}

fn row(node: NodeId, phase: &str, site: &Site, root: &str) -> Value {
    let graph = &site.program.graph;
    let span = graph.span(node);
    json!({
        "location": super::source::reference(graph, node, root),
        "line": span.line,
        "column": span.col,
        "phase": phase,
        "actor": graph.form(node),
        "transitions": site.cost.transitions,
        "elapsed_ns": site.cost.elapsed_ns,
    })
}

pub(super) fn run(path: &str, options: &[&str]) -> ExitCode {
    let (mut limit, mut top) = (2_000_000, 20);
    for pair in options.chunks(2) {
        match pair {
            [option @ ("--limit" | "--top"), number] => {
                let Ok(value) = number.parse::<usize>() else {
                    eprintln!("{option} requires a nonnegative integer");
                    return ExitCode::from(2);
                };
                if *option == "--limit" {
                    limit = value;
                } else {
                    top = value;
                }
            }
            _ => {
                eprintln!("usage: helix profile FILE [--limit N] [--top N]");
                return ExitCode::from(2);
            }
        }
    }

    let started = Instant::now();
    let mut vm = match super::inspection::load(path) {
        Ok(vm) => vm,
        Err(code) => return code,
    };
    let load_ns = started.elapsed().as_nanos();
    let mut sites = Sites::new();
    let mut other = Cost::default();
    let mut advances = 0;
    let started = Instant::now();
    while advances < limit && matches!(vm.status(), Status::Ready | Status::Running) {
        vm.advance_with(|evaluator| observe(evaluator, &mut sites, &mut other));
        advances += 1;
    }
    let run_ns = started.elapsed().as_nanos();
    let limited = matches!(vm.status(), Status::Ready | Status::Running);
    let root = if path == "-" { "<root>" } else { path };
    let mut ranked = sites
        .iter()
        .map(|(&(_, node, phase), site)| (node, phase, site))
        .collect::<Vec<_>>();
    // Break count ties independently of timing, and format only the requested rows.
    ranked
        .sort_by_cached_key(|&(node, phase, site)| (site.program.graph.source(node), node, phase));
    let render = |&(node, phase, site): &(NodeId, &str, &Site)| row(node, phase, site, root);
    ranked.sort_by_key(|(_, _, site)| std::cmp::Reverse(site.cost.transitions));
    let by_count: Vec<_> = ranked.iter().take(top).map(render).collect();
    ranked.sort_by_key(|(_, _, site)| std::cmp::Reverse(site.cost.elapsed_ns));
    let by_time: Vec<_> = ranked.iter().take(top).map(render).collect();
    let attributed_transitions: usize = sites.values().map(|site| site.cost.transitions).sum();
    let report = json!({
        "format": "helix.profile.v1",
        "status": vm.status().as_str(),
        "stop": if limited { "limit" } else { vm.status().as_str() },
        "load_ns": load_ns,
        "run_ns": run_ns,
        "advances": advances,
        "attributed_transitions": attributed_transitions,
        "transition_limit": limit,
        "site_count": sites.len(),
        "pending_operation": vm.pending_effect().map(|effect| &effect.operation),
        "other": {"transitions": other.transitions, "elapsed_ns": other.elapsed_ns},
        "by_time": by_time,
        "by_count": by_count,
        "error": if vm.status() == Status::Error { crate::tooling::source::json(&vm.state()["error"]) } else { Value::Null },
    });
    let mut stdout = io::stdout().lock();
    let written = serde_json::to_writer(&mut stdout, &report)
        .and_then(|()| writeln!(stdout).map_err(serde_json::Error::io));
    match written {
        Ok(()) if limited || vm.status() == Status::Error => ExitCode::FAILURE,
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("cannot write profile: {error}");
            ExitCode::from(2)
        }
    }
}
