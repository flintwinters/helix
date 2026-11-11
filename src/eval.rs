//! Evaluation of vector forms (actor first) over the object graph.
//!
//! Symbols resolve lexically through parent links: enclosing maps, the parameters of an enclosing
//! `fn`, and the bindings of an enclosing `let`. Nodes inside a function body depend on the call's
//! arguments, so they are never memoized, and they are cycle-checked per call depth; recursion is
//! bounded by `MAX_DEPTH`.

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

/// Owns the graph while it is evaluated: memoized results, the nodes currently being evaluated
/// (with the call depth they are evaluated at), and the stack of active function calls.
pub struct Evaluator {
    graph: Graph,
    memo: HashMap<NodeId, Yaml>,
    active: HashSet<(NodeId, usize)>,
    frames: Vec<Frame>,
}

impl Evaluator {
    pub fn new(graph: Graph) -> Evaluator {
        Evaluator { graph, memo: HashMap::new(), active: HashSet::new(), frames: Vec::new() }
    }

    fn err(&self, id: NodeId, kind: &'static str, message: String) -> HelixError {
        HelixError { kind, message, path: Some(self.graph.path(id)), span: self.graph.span(id) }
    }

    /// Evaluate a node to plain YAML data. A node that needs its own value within one call is a `cycle`.
    pub fn eval(&mut self, id: NodeId) -> Result<Yaml, HelixError> {
        let local = self.in_function(id);
        if let Some(v) = self.memo.get(&id) {
            return Ok(v.clone());
        }
        let key = (id, if local { self.frames.len() } else { 0 });
        if !self.active.insert(key) {
            return Err(self.err(id, "cycle", "value depends on itself".into()));
        }
        let result = self.eval_uncached(id);
        self.active.remove(&key);
        if let (false, Ok(v)) = (local, &result) {
            self.memo.insert(id, v.clone());
        }
        result
    }

    fn eval_uncached(&mut self, id: NodeId) -> Result<Yaml, HelixError> {
        enum Shape {
            Scalar(Yaml),
            Seq(Vec<NodeId>, Option<Actor>),
            Map(Vec<(String, NodeId)>),
        }
        let shape = match self.graph.kind(id) {
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

    /// A plain (unquoted) string, which is a symbol in expression position.
    fn symbol(&self, id: NodeId) -> Option<&str> {
        match self.graph.kind(id) {
            Kind::Scalar { value: Yaml::String(name), plain: true } => Some(name),
            _ => None,
        }
    }

    /// The symbol heading a sequence (quoted heads are data).
    fn form(&self, id: NodeId) -> Option<&str> {
        let Kind::Seq(items) = self.graph.kind(id) else { return None };
        self.symbol(*items.first()?)
    }

    /// A sequence is an expression iff its head names a builtin or a user function.
    fn actor(&self, items: &[NodeId]) -> Option<Actor> {
        let head = *items.first()?;
        let name = self.symbol(head)?;
        if builtins::arity(name).is_some() {
            return Some(Actor::Builtin(name.to_string()));
        }
        self.function(head).map(Actor::Function)
    }

    /// The well-formed `[fn, params, body]` that `id` designates: itself, or the one a symbol names.
    fn function(&self, id: NodeId) -> Option<NodeId> {
        let target = match self.symbol(id) {
            Some(name) => match self.resolve(id, name)? {
                Binding::Node(target) => target,
                Binding::Value(_) => return None,
            },
            None => id,
        };
        let Kind::Seq(items) = self.graph.kind(target) else { return None };
        (items.len() == 3 && self.form(target) == Some("fn")).then_some(target)
    }

    /// Whether `id` sits inside some `fn` form, so its value depends on a call.
    fn in_function(&self, id: NodeId) -> bool {
        let mut cur = self.graph.parent(id);
        while let Some(parent) = cur {
            if self.form(parent) == Some("fn") {
                return true;
            }
            cur = self.graph.parent(parent);
        }
        false
    }

    /// The parameter names of a `fn` form.
    fn parameters(&self, function: NodeId) -> Result<Vec<&str>, HelixError> {
        let Kind::Seq(items) = self.graph.kind(function) else { unreachable!("a function is a sequence") };
        let malformed = || self.err(items[1], "type", "parameters must be a list of names".into());
        let Kind::Seq(params) = self.graph.kind(items[1]) else { return Err(malformed()) };
        params.iter().map(|&p| self.symbol(p).ok_or_else(malformed)).collect()
    }

    /// Lexical resolution from `from`: the nearest enclosing map entry, parameter of an enclosing
    /// `fn` (read from its innermost active call), or binding of an enclosing `let` named `name`.
    fn resolve(&self, from: NodeId, name: &str) -> Option<Binding> {
        let entry = |entries: &[(String, NodeId)]| entries.iter().find(|(k, _)| k == name).map(|(_, target)| Binding::Node(*target));
        let mut child = from;
        while let Some(id) = self.graph.parent(child) {
            let found = match self.graph.kind(id) {
                Kind::Map(entries) => entry(entries),
                // Parameters and `let` bindings are in scope only in the body, the third item.
                Kind::Seq(items) if items.get(2) == Some(&child) => match (self.form(id), self.graph.kind(items[1])) {
                    (Some("fn"), _) => self.parameters(id).ok().and_then(|params| params.iter().position(|p| *p == name)).and_then(|i| {
                        let frame = self.frames.iter().rev().find(|f| f.function == id)?;
                        Some(Binding::Value(frame.args[i].clone()))
                    }),
                    (Some("let"), Kind::Map(entries)) => entry(entries),
                    _ => None,
                },
                _ => None,
            };
            if found.is_some() {
                return found;
            }
            child = id;
        }
        None
    }

    fn apply(&mut self, id: NodeId, actor: Actor, args: &[NodeId]) -> Result<Yaml, HelixError> {
        if let Actor::Builtin(name) = &actor {
            let (min, max) = builtins::arity(name).expect("actor is a builtin");
            if args.len() < min || max.is_some_and(|m| args.len() > m) {
                let expected = match max {
                    Some(m) if m == min => arguments(m),
                    Some(m) => format!("{min} to {}", arguments(m)),
                    None => format!("at least {}", arguments(min)),
                };
                return Err(self.err(id, "arity", format!("`{name}` takes {expected}, got {}", args.len())));
            }
            if builtins::is_form(name) {
                return self.apply_form(id, name, args);
            }
        }
        let values = args.iter().map(|&arg| self.value_arg(arg)).collect::<Result<Vec<_>, _>>()?;
        match actor {
            Actor::Function(function) => self.call(id, function, values),
            Actor::Builtin(name) => builtins::apply(&name, &values).map_err(|fault| self.err(fault.arg.map_or(id, |i| args[i]), fault.kind, fault.message)),
        }
    }

    /// Run a function body with `args` bound to its parameters; `site` is blamed for a wrong
    /// argument count or a call nested deeper than `MAX_DEPTH`.
    fn call(&mut self, site: NodeId, function: NodeId, args: Vec<Yaml>) -> Result<Yaml, HelixError> {
        let n = self.parameters(function)?.len();
        if n != args.len() {
            let message = format!("function `{}` takes {}, got {}", self.graph.path(function), arguments(n), args.len());
            return Err(self.err(site, "arity", message));
        }
        if self.frames.len() >= MAX_DEPTH {
            return Err(self.err(site, "depth", format!("calls nested deeper than {MAX_DEPTH}")));
        }
        let Kind::Seq(items) = self.graph.kind(function) else { unreachable!("a function is a sequence") };
        let body = items[2];
        self.frames.push(Frame { function, args });
        let result = self.value_arg(body);
        self.frames.pop();
        result
    }

    /// Call the function designated by `designator` (an inline `fn` or a name) with `values`.
    fn call_arg(&mut self, designator: NodeId, values: Vec<Yaml>) -> Result<Yaml, HelixError> {
        let function = self.function(designator).ok_or_else(|| self.err(designator, "type", "expected a function (`[fn, [params], body]` or its name)".into()))?;
        self.call(designator, function, values)
    }

    /// Forms with their own evaluation order: `if`, `cond`, `and`, `or` evaluate only what they
    /// need; `fn` yields its own source; `let` evaluates its body with the bindings in scope; the
    /// iteration forms call the function designated by their first argument.
    fn apply_form(&mut self, id: NodeId, actor: &str, args: &[NodeId]) -> Result<Yaml, HelixError> {
        match actor {
            "if" => {
                let condition = self.condition(args[0])?;
                self.value_arg(args[if condition { 1 } else { 2 }])
            }
            "cond" => {
                if args.len().is_multiple_of(2) {
                    return Err(self.err(id, "arity", "`cond` takes condition/value pairs followed by a default".into()));
                }
                for pair in args.chunks_exact(2) {
                    if self.condition(pair[0])? {
                        return self.value_arg(pair[1]);
                    }
                }
                self.value_arg(args[args.len() - 1])
            }
            "and" | "or" => {
                let stop_on = actor == "or";
                for &arg in args {
                    if self.condition(arg)? == stop_on {
                        return Ok(Yaml::Boolean(stop_on));
                    }
                }
                Ok(Yaml::Boolean(!stop_on))
            }
            "fn" => {
                self.parameters(id)?;
                Ok(self.raw(id))
            }
            "let" => {
                if !matches!(self.graph.kind(args[0]), Kind::Map(_)) {
                    return Err(self.err(args[0], "type", "`let` bindings must be a map".into()));
                }
                self.value_arg(args[1])
            }
            "map" => self.list_arg(args[1])?.into_iter().map(|item| self.call_arg(args[0], vec![item])).collect::<Result<_, _>>().map(Yaml::Array),
            "filter" => {
                let mut kept = Vec::new();
                for item in self.list_arg(args[1])? {
                    let keep = self.call_arg(args[0], vec![item.clone()])?;
                    if self.truth(&keep, args[0])? {
                        kept.push(item);
                    }
                }
                Ok(Yaml::Array(kept))
            }
            "fold" => {
                let init = self.value_arg(args[1])?;
                self.list_arg(args[2])?.into_iter().try_fold(init, |acc, item| self.call_arg(args[0], vec![acc, item]))
            }
            "each" => {
                for item in self.list_arg(args[1])? {
                    self.call_arg(args[0], vec![item])?;
                }
                Ok(Yaml::Null)
            }
            "until" => {
                let mut state = self.value_arg(args[2])?;
                for _ in 0..MAX_STEPS {
                    let done = self.call_arg(args[0], vec![state.clone()])?;
                    if self.truth(&done, args[0])? {
                        return Ok(state);
                    }
                    state = self.call_arg(args[1], vec![state])?;
                }
                Err(self.err(id, "limit", format!("`until` did not finish within {MAX_STEPS} steps")))
            }
            _ => unreachable!("`{actor}` is not a form"),
        }
    }

    /// A subtree as data that re-reads as the same code: plain scalars are emitted verbatim and
    /// quoted ones stay quoted (via `Yaml::Real`, which the emitter prints as-is).
    fn raw(&self, id: NodeId) -> Yaml {
        match self.graph.kind(id) {
            Kind::Scalar { value: Yaml::String(text), plain } => Yaml::Real(if *plain { text.clone() } else { emit::quoted(text) }),
            Kind::Scalar { value, .. } => value.clone(),
            Kind::Seq(items) => Yaml::Array(items.iter().map(|&c| self.raw(c)).collect()),
            Kind::Map(entries) => Yaml::Hash(entries.iter().map(|(k, c)| (Yaml::String(k.clone()), self.raw(*c))).collect()),
        }
    }

    /// A value that must be a boolean; `blame` is the node that produced it.
    fn truth(&self, value: &Yaml, blame: NodeId) -> Result<bool, HelixError> {
        builtins::boolean(value, 0).map_err(|fault| self.err(blame, fault.kind, fault.message))
    }

    fn condition(&mut self, id: NodeId) -> Result<bool, HelixError> {
        let value = self.value_arg(id)?;
        self.truth(&value, id)
    }

    fn list_arg(&mut self, id: NodeId) -> Result<Vec<Yaml>, HelixError> {
        let value = self.value_arg(id)?;
        builtins::list(value, 0).map_err(|fault| self.err(id, fault.kind, fault.message))
    }

    /// Evaluate an expression argument: plain strings are symbols. Functions are not values; they
    /// may only head a call or be passed where a form expects one.
    fn value_arg(&mut self, id: NodeId) -> Result<Yaml, HelixError> {
        let target = match self.symbol(id) {
            Some(name) => match self.resolve(id, name) {
                Some(Binding::Node(target)) => target,
                Some(Binding::Value(value)) => return Ok(value),
                None => return Err(self.err(id, "unbound", format!("unbound symbol `{name}`"))),
            },
            None => id,
        };
        if self.form(target) == Some("fn") {
            return Err(self.err(id, "type", "a function is not a value; call it or pass it to a form that takes one".into()));
        }
        self.eval(target)
    }
}

/// `1 argument`, `2 arguments`.
fn arguments(n: usize) -> String {
    if n == 1 { "1 argument".into() } else { format!("{n} arguments") }
}
