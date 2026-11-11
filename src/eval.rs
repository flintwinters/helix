//! Evaluation of vector forms (actor first) over the object graph.
//!
//! Symbols resolve lexically through parent links: enclosing maps, the parameters of an enclosing
//! `fn`, and the bindings of an enclosing `let`. Nodes inside a function body depend on the call's
//! arguments, so they are never memoized and never cycle-checked (recursion is bounded by `MAX_DEPTH`).

use std::collections::{HashMap, HashSet};

use yaml_rust2::yaml::Hash;
use yaml_rust2::Yaml;

use crate::builtins;
use crate::emit;
use crate::error::HelixError;
use crate::graph::{Graph, Kind, NodeId};

/// Nested user-function calls allowed before failing with a `depth` error.
const MAX_DEPTH: usize = 200;
/// Iterations `until` allows before failing with a `limit` error.
const MAX_STEPS: usize = 100_000;

/// What a symbol resolves to: a node to evaluate, or an argument value of an active call.
enum Binding {
    Node(NodeId),
    Value(Yaml),
}

/// What the head of a vector form names.
enum Actor {
    Builtin(String),
    Function(NodeId),
}

/// An active user-function call: the `fn` node and the evaluated arguments, in parameter order.
struct Frame {
    function: NodeId,
    args: Vec<Yaml>,
}

/// Owns the graph while it is evaluated: memoized results, the nodes currently being evaluated,
/// and the stack of active function calls.
pub struct Evaluator {
    graph: Graph,
    memo: HashMap<NodeId, Yaml>,
    active: HashSet<NodeId>,
    frames: Vec<Frame>,
}

impl Evaluator {
    pub fn new(graph: Graph) -> Evaluator {
        Evaluator { graph, memo: HashMap::new(), active: HashSet::new(), frames: Vec::new() }
    }

    fn graph(&self) -> &Graph {
        &self.graph
    }

    fn err(&self, id: NodeId, kind: &'static str, message: String) -> HelixError {
        let g = self.graph();
        HelixError { kind, message, path: Some(g.path(id)), span: g.span(id) }
    }

    /// Evaluate a node to plain YAML data, memoized, with cycle detection.
    pub fn eval(&mut self, id: NodeId) -> Result<Yaml, HelixError> {
        if self.in_function(id) {
            return self.eval_uncached(id);
        }
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
            Seq(Vec<NodeId>, Option<Actor>),
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
            Shape::Seq(items, Some(actor)) => self.apply(id, actor, &items[1..]),
            Shape::Seq(items, None) => Ok(Yaml::Array(items.into_iter().map(|c| self.eval(c)).collect::<Result<_, _>>()?)),
        }
    }

    /// The name heading a sequence, when the head is a plain string (quoted heads are data).
    fn form(&self, id: NodeId) -> Option<&str> {
        let Kind::Seq(items) = self.graph().kind(id) else { return None };
        match self.graph().kind(*items.first()?) {
            Kind::Scalar { value: Yaml::String(name), plain: true } => Some(name),
            _ => None,
        }
    }

    /// A sequence is an expression iff its head names a builtin or resolves to a user function.
    fn actor(&self, items: &[NodeId]) -> Option<Actor> {
        let head = *items.first()?;
        let name = self.form(self.graph().parent(head)?)?;
        if builtins::arity(name).is_some() {
            return Some(Actor::Builtin(name.to_string()));
        }
        match self.resolve(head, name)? {
            Binding::Node(target) if self.form(target) == Some("fn") => Some(Actor::Function(target)),
            _ => None,
        }
    }

    /// Whether `id` sits inside some `fn` form, so its value depends on a call.
    fn in_function(&self, id: NodeId) -> bool {
        let mut cur = self.graph().parent(id);
        while let Some(parent) = cur {
            if self.form(parent) == Some("fn") {
                return true;
            }
            cur = self.graph().parent(parent);
        }
        false
    }

    /// The names a `fn` form declares, or `None` when its parameter list is malformed.
    fn parameters(&self, function: NodeId) -> Option<Vec<String>> {
        let Kind::Seq(items) = self.graph().kind(function) else { return None };
        let Kind::Seq(params) = self.graph().kind(*items.get(1)?) else { return None };
        params
            .iter()
            .map(|&p| match self.graph().kind(p) {
                Kind::Scalar { value: Yaml::String(name), plain: true } => Some(name.clone()),
                _ => None,
            })
            .collect()
    }

    /// Lexical resolution from `from`: the nearest enclosing map entry, parameter of an enclosing
    /// `fn` (read from its active call), or binding of an enclosing `let` (body only) named `name`.
    fn resolve(&self, from: NodeId, name: &str) -> Option<Binding> {
        let mut child = from;
        while let Some(id) = self.graph().parent(child) {
            match self.graph().kind(id) {
                Kind::Map(entries) => {
                    if let Some((_, target)) = entries.iter().find(|(k, _)| k == name) {
                        return Some(Binding::Node(*target));
                    }
                }
                Kind::Seq(items) if items.get(2) == Some(&child) => match self.form(id) {
                    Some("fn") => {
                        if let Some(i) = self.parameters(id)?.iter().position(|p| p == name) {
                            let frame = self.frames.iter().rev().find(|f| f.function == id)?;
                            return Some(Binding::Value(frame.args[i].clone()));
                        }
                    }
                    Some("let") => {
                        if let Kind::Map(entries) = self.graph().kind(items[1]) {
                            if let Some((_, target)) = entries.iter().find(|(k, _)| k == name) {
                                return Some(Binding::Node(*target));
                            }
                        }
                    }
                    _ => {}
                },
                _ => {}
            }
            child = id;
        }
        None
    }

    /// Evaluate one expression.
    fn apply(&mut self, id: NodeId, actor: Actor, args: &[NodeId]) -> Result<Yaml, HelixError> {
        let (min, max) = match &actor {
            Actor::Builtin(name) => builtins::arity(name).expect("actor is a builtin"),
            Actor::Function(function) => {
                let n = self.parameters(*function).ok_or_else(|| self.bad_parameters(*function))?.len();
                (n, Some(n))
            }
        };
        if args.len() < min || max.is_some_and(|m| args.len() > m) {
            let expected = match max {
                Some(1) => "1 argument".to_string(),
                Some(m) if m == min => format!("{min} arguments"),
                _ => format!("at least {min} arguments"),
            };
            let name = self.form(id).expect("an expression has a head");
            return Err(self.err(id, "arity", format!("`{name}` takes {expected}, got {}", args.len())));
        }
        match actor {
            Actor::Function(function) => {
                let values = args.iter().map(|&arg| self.value_arg(arg)).collect::<Result<Vec<_>, _>>()?;
                self.call(id, function, values)
            }
            Actor::Builtin(name) if builtins::is_form(&name) => self.apply_form(id, &name, args),
            Actor::Builtin(name) => {
                let values = args.iter().map(|&arg| self.value_arg(arg)).collect::<Result<Vec<_>, _>>()?;
                builtins::apply(&name, &values).map_err(|fault| self.fault(id, args, fault))
            }
        }
    }

    fn bad_parameters(&self, function: NodeId) -> HelixError {
        let Kind::Seq(items) = self.graph().kind(function) else { unreachable!("a function is a sequence") };
        self.err(items[1], "type", "parameters must be a list of names".into())
    }

    /// Run a function body with `args` bound to its parameters. `site` is the expression to blame
    /// for a call that exceeds `MAX_DEPTH`.
    fn call(&mut self, site: NodeId, function: NodeId, args: Vec<Yaml>) -> Result<Yaml, HelixError> {
        let Kind::Seq(items) = self.graph().kind(function) else { unreachable!("a function is a sequence") };
        let body = items[2];
        if self.frames.len() >= MAX_DEPTH {
            return Err(self.err(site, "depth", format!("calls nested deeper than {MAX_DEPTH}")));
        }
        self.frames.push(Frame { function, args });
        let result = self.value_arg(body);
        self.frames.pop();
        result
    }

    /// A function designator: an inline `fn` form, or a symbol naming one.
    fn function_arg(&self, id: NodeId) -> Result<NodeId, HelixError> {
        let target = match self.graph().kind(id) {
            Kind::Scalar { value: Yaml::String(name), plain: true } => match self.resolve(id, name) {
                Some(Binding::Node(target)) => target,
                Some(Binding::Value(_)) | None => return Err(self.err(id, "unbound", format!("no function named `{name}`"))),
            },
            _ => id,
        };
        if self.form(target) != Some("fn") {
            return Err(self.err(id, "type", "expected a function (`[fn, [params], body]` or its name)".into()));
        }
        Ok(target)
    }

    /// Call the function designated by `args[at]` with `values`, as part of the form at `id`.
    fn call_arg(&mut self, id: NodeId, args: &[NodeId], at: usize, values: Vec<Yaml>) -> Result<Yaml, HelixError> {
        let function = self.function_arg(args[at])?;
        let n = self.parameters(function).ok_or_else(|| self.bad_parameters(function))?.len();
        if n != values.len() {
            return Err(self.err(args[at], "arity", format!("function takes {n} arguments, but `{}` passes {}", self.form(id).unwrap_or(""), values.len())));
        }
        self.call(id, function, values)
    }

    /// Blame the argument a builtin names, or the whole expression.
    fn fault(&self, id: NodeId, args: &[NodeId], fault: builtins::Fault) -> HelixError {
        self.err(fault.arg.map_or(id, |i| args[i]), fault.kind, fault.message)
    }

    /// Forms with their own evaluation order.
    /// `if`, `cond`, `and`, `or` evaluate only what they need; every condition evaluated must be a boolean.
    /// `fn` yields its own source, `let` evaluates its body with the bindings in scope, and
    /// `map`/`filter`/`fold`/`each`/`until` call a function designator (see `function_arg`).
    fn apply_form(&mut self, id: NodeId, actor: &str, args: &[NodeId]) -> Result<Yaml, HelixError> {
        match actor {
            "if" => {
                let condition = self.boolean_arg(id, args, 0)?;
                self.value_arg(if condition { args[1] } else { args[2] })
            }
            "cond" => {
                if args.len() % 2 == 0 {
                    return Err(self.err(id, "arity", "`cond` takes condition/value pairs followed by a default".into()));
                }
                for at in (0..args.len() - 1).step_by(2) {
                    if self.boolean_arg(id, args, at)? {
                        return self.value_arg(args[at + 1]);
                    }
                }
                self.value_arg(args[args.len() - 1])
            }
            "and" | "or" => {
                let stop_on = actor == "or";
                for index in 0..args.len() {
                    if self.boolean_arg(id, args, index)? == stop_on {
                        return Ok(Yaml::Boolean(stop_on));
                    }
                }
                Ok(Yaml::Boolean(!stop_on))
            }
            "fn" => {
                self.parameters(id).ok_or_else(|| self.bad_parameters(id))?;
                Ok(self.raw(id))
            }
            "let" => {
                if !matches!(self.graph().kind(args[0]), Kind::Map(_)) {
                    return Err(self.err(args[0], "type", "`let` bindings must be a map".into()));
                }
                self.value_arg(args[1])
            }
            _ => self.iterate(id, actor, args),
        }
    }

    /// `map`, `filter`, `fold`, `each` and `until`: the function is `args[0]`, the data follows.
    fn iterate(&mut self, id: NodeId, actor: &str, args: &[NodeId]) -> Result<Yaml, HelixError> {
        if actor == "until" {
            let mut state = self.value_arg(args[2])?;
            for _ in 0..MAX_STEPS {
                let done = self.call_arg(id, args, 0, vec![state.clone()])?;
                if builtins::boolean(&done, 0).map_err(|fault| self.fault(id, args, fault))? {
                    return Ok(state);
                }
                state = self.call_arg(id, args, 1, vec![state])?;
            }
            return Err(self.err(id, "limit", format!("`until` did not finish within {MAX_STEPS} steps")));
        }
        let list_at = args.len() - 1;
        let list = self.value_arg(args[list_at])?;
        let items = builtins::list(&list, list_at).map_err(|fault| self.fault(id, args, fault))?;
        let mut acc = if actor == "fold" { Some(self.value_arg(args[1])?) } else { None };
        let mut out = Vec::new();
        for item in items {
            let result = match acc.take() {
                Some(carried) => self.call_arg(id, args, 0, vec![carried, item.clone()])?,
                None => self.call_arg(id, args, 0, vec![item.clone()])?,
            };
            match actor {
                "map" => out.push(result),
                "filter" => {
                    if builtins::boolean(&result, 0).map_err(|fault| self.fault(id, args, fault))? {
                        out.push(item.clone());
                    }
                }
                "fold" => acc = Some(result),
                _ => {}
            }
        }
        Ok(match actor {
            "map" | "filter" => Yaml::Array(out),
            "fold" => acc.unwrap_or(Yaml::Null),
            _ => Yaml::Null,
        })
    }

    /// A subtree as data that re-reads as the same code: plain scalars are emitted verbatim and
    /// quoted ones stay quoted (via `Yaml::Real`, which the emitter prints as-is).
    fn raw(&self, id: NodeId) -> Yaml {
        match self.graph().kind(id) {
            Kind::Scalar { value: Yaml::String(text), plain } => Yaml::Real(if *plain { text.clone() } else { emit::quoted(text) }),
            Kind::Scalar { value, .. } => value.clone(),
            Kind::Seq(items) => Yaml::Array(items.iter().map(|&c| self.raw(c)).collect()),
            Kind::Map(entries) => Yaml::Hash(entries.iter().map(|(k, c)| (Yaml::String(k.clone()), self.raw(*c))).collect()),
        }
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
            Some(name) => match self.resolve(id, &name) {
                Some(Binding::Node(target)) => self.eval(target),
                Some(Binding::Value(value)) => Ok(value),
                None => Err(self.err(id, "unbound", format!("unbound symbol `{name}`"))),
            },
            None => self.eval(id),
        }
    }
}
