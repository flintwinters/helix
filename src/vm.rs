//! The VM: evaluates vector forms (actor first) over the object graph and serializes its state to YAML.

use std::collections::{HashMap, HashSet};

use yaml_rust2::yaml::Hash;
use yaml_rust2::Yaml;

use crate::builtins;
use crate::emit;
use crate::error::{s, HelixError};
use crate::graph::{Graph, Kind, NodeId, ROOT};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Status {
    Ready,
    Running,
    Finished,
    Error,
    Signaled,
}

impl Status {
    pub fn as_str(self) -> &'static str {
        match self {
            Status::Ready => "ready",
            Status::Running => "running",
            Status::Finished => "finished",
            Status::Error => "error",
            Status::Signaled => "signaled",
        }
    }
}

pub struct Vm {
    graph: Option<Graph>,
    status: Status,
    result: Option<Yaml>,
    error: Option<HelixError>,
    memo: HashMap<NodeId, Yaml>,
    active: HashSet<NodeId>,
}

impl Vm {
    pub fn load(src: &str) -> Vm {
        let mut vm = Vm { graph: None, status: Status::Ready, result: None, error: None, memo: HashMap::new(), active: HashSet::new() };
        match Graph::parse(src) {
            Ok(g) => vm.graph = Some(g),
            Err(e) => vm.fail(e),
        }
        vm
    }

    pub fn status(&self) -> Status {
        self.status
    }

    pub fn run(&mut self) {
        if self.status != Status::Ready {
            return;
        }
        self.status = Status::Running;
        match self.eval(ROOT) {
            Ok(v) => {
                self.result = Some(v);
                self.status = Status::Finished;
            }
            Err(e) => self.fail(e),
        }
    }

    /// The serialized VM state: `status` plus `root` (finished) or `error` (error).
    pub fn state(&self) -> Yaml {
        let mut h = Hash::new();
        h.insert(s("status"), s(self.status.as_str()));
        if let Some(r) = &self.result {
            h.insert(s("root"), r.clone());
        }
        if let Some(e) = &self.error {
            h.insert(s("error"), e.to_yaml());
        }
        Yaml::Hash(h)
    }

    pub fn state_string(&self) -> String {
        emit::document(&self.state())
    }

    fn fail(&mut self, e: HelixError) {
        self.status = Status::Error;
        self.error = Some(e);
    }

    fn graph(&self) -> &Graph {
        self.graph.as_ref().expect("graph is present unless status is error")
    }

    fn err(&self, id: NodeId, kind: &'static str, message: String) -> HelixError {
        let g = self.graph();
        HelixError { kind, message, path: Some(g.path(id)), span: g.span(id) }
    }

    /// Evaluate a node to plain YAML data, memoized, with cycle detection.
    fn eval(&mut self, id: NodeId) -> Result<Yaml, HelixError> {
        if let Some(v) = self.memo.get(&id) {
            return Ok(v.clone());
        }
        if !self.active.insert(id) {
            return Err(self.err(id, "cycle", "value depends on itself".into()));
        }
        let result = self.eval_uncached(id);
        self.active.remove(&id);
        let v = result?;
        self.memo.insert(id, v.clone());
        Ok(v)
    }

    fn eval_uncached(&mut self, id: NodeId) -> Result<Yaml, HelixError> {
        enum Shape {
            Scalar(Yaml),
            Seq(Vec<NodeId>, Option<String>),
            Map(Vec<(String, NodeId)>),
        }
        let shape = match self.graph().kind(id) {
            Kind::Scalar { value, .. } => Shape::Scalar(value.clone()),
            Kind::Map(entries) => Shape::Map(entries.clone()),
            Kind::Seq(items) => Shape::Seq(items.clone(), self.actor(items)),
        };
        match shape {
            Shape::Scalar(v) => Ok(v),
            Shape::Map(entries) => {
                let mut h = Hash::new();
                for (k, child) in entries {
                    h.insert(Yaml::String(k), self.eval(child)?);
                }
                Ok(Yaml::Hash(h))
            }
            Shape::Seq(items, Some(actor)) => self.apply(id, &actor, &items[1..]),
            Shape::Seq(items, None) => Ok(Yaml::Array(items.into_iter().map(|c| self.eval(c)).collect::<Result<_, _>>()?)),
        }
    }

    fn actor(&self, items: &[NodeId]) -> Option<String> {
        match self.graph().kind(*items.first()?) {
            Kind::Scalar { value: Yaml::String(name), plain: true } if builtins::arity(name).is_some() => Some(name.clone()),
            _ => None,
        }
    }

    /// Evaluate one expression. A sequence is an expression iff its head is a plain scalar naming a builtin.
    fn apply(&mut self, id: NodeId, actor: &str, args: &[NodeId]) -> Result<Yaml, HelixError> {
        let (min, max) = builtins::arity(actor).expect("actor is a builtin");
        if args.len() < min || max.is_some_and(|m| args.len() > m) {
            let expected = match max {
                Some(1) => "1 argument".to_string(),
                Some(m) if m == min => format!("{min} arguments"),
                _ => format!("at least {min} arguments"),
            };
            return Err(self.err(id, "arity", format!("`{actor}` takes {expected}, got {}", args.len())));
        }
        if builtins::is_lazy(actor) {
            return self.apply_lazy(id, actor, args);
        }
        let values = args.iter().map(|&arg| self.value_arg(arg)).collect::<Result<Vec<_>, _>>()?;
        builtins::apply(actor, &values).map_err(|fault| self.fault(id, args, fault))
    }

    /// Blame the argument a builtin names, or the whole expression.
    fn fault(&self, id: NodeId, args: &[NodeId], fault: builtins::Fault) -> HelixError {
        self.err(fault.arg.map_or(id, |i| args[i]), fault.kind, fault.message)
    }

    /// `if` evaluates only the branch it selects; `and`/`or` stop at the first `false`/`true`.
    /// Every operand that is evaluated must be a boolean, and the result is a boolean.
    fn apply_lazy(&mut self, id: NodeId, actor: &str, args: &[NodeId]) -> Result<Yaml, HelixError> {
        if actor == "if" {
            let condition = self.boolean_arg(id, args, 0)?;
            return self.value_arg(if condition { args[1] } else { args[2] });
        }
        let stop_on = actor == "or";
        for index in 0..args.len() {
            if self.boolean_arg(id, args, index)? == stop_on {
                return Ok(Yaml::Boolean(stop_on));
            }
        }
        Ok(Yaml::Boolean(!stop_on))
    }

    fn boolean_arg(&mut self, id: NodeId, args: &[NodeId], index: usize) -> Result<bool, HelixError> {
        let value = self.value_arg(args[index])?;
        builtins::boolean(&value, index).map_err(|fault| self.fault(id, args, fault))
    }

    /// Evaluate an expression argument: plain strings are symbols resolved through parent links.
    fn value_arg(&mut self, id: NodeId) -> Result<Yaml, HelixError> {
        let symbol = match self.graph().kind(id) {
            Kind::Scalar { value: Yaml::String(name), plain: true } => Some(name.clone()),
            _ => None,
        };
        match symbol {
            Some(name) => {
                let target = self.graph().lookup(id, &name).ok_or_else(|| self.err(id, "unbound", format!("unbound symbol `{name}`")))?;
                self.eval(target)
            }
            None => self.eval(id),
        }
    }
}
