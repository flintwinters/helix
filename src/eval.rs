//! Evaluation of vector forms (actor first) over the object graph.
//!
//! Symbols resolve lexically through parent links: enclosing maps, the parameters of an enclosing
//! `fn`, and the bindings of an enclosing `let`. Nodes inside a function body depend on the call's
//! arguments, so they are never memoized, and they are cycle-checked per call depth; recursion is
//! bounded by `MAX_DEPTH`.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use yaml_rust2::yaml::Hash;
use yaml_rust2::Yaml;

use crate::builtins::{self, Fault};
use crate::emit;
use crate::error::{s, HelixError};
use crate::graph::{Graph, Kind, NodeId, ROOT};
use crate::link::{self, Modules};

/// Nested user-function calls allowed before failing with a `depth` error.
const MAX_DEPTH: usize = 200;
/// Iterations `until` allows before failing with a `limit` error.
const MAX_STEPS: usize = 100_000;
const MAX_WORK: usize = 2_000_000;
const MAX_VALUES: usize = 8_000_000;
const MAX_EVAL_DEPTH: usize = 512;

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
    /// Predicate sources (refinement checks) grafted into the graph, by their emitted text.
    grafts: HashMap<String, NodeId>,
    /// The modules the program imports.
    modules: Modules,
    closing: HashSet<NodeId>,
    type_time: bool,
    remaining_work: usize,
    remaining_values: usize,
}

impl Evaluator {
    /// Give CLI and library callers the same bounded stack, independent of the host thread.
    pub(crate) fn on_stack<T: Send>(&mut self, operation: impl FnOnce(&mut Self) -> Result<T, HelixError> + Send) -> Result<T, HelixError> {
        let result = std::thread::scope(|scope| {
            match std::thread::Builder::new().name("helix-evaluator".into()).stack_size(32 * 1024 * 1024)
                .spawn_scoped(scope, || operation(self)) {
                Ok(worker) => worker.join().map_err(|_| "evaluation worker panicked".to_string()),
                Err(error) => Err(format!("cannot start evaluation worker: {error}")),
            }
        });
        result.map_err(|message| self.err(ROOT, "limit", message))?
    }

    /// Take a parsed program and link its imports; `main` is the program's file, when it has one.
    pub fn new(mut graph: Graph, main: Option<&Path>, roots: Option<&[PathBuf]>) -> Result<Evaluator, HelixError> {
        let modules = link::link(&mut graph, main, roots)?;
        Ok(Evaluator { graph, memo: HashMap::new(), active: HashSet::new(), frames: Vec::new(), grafts: HashMap::new(), modules, closing: HashSet::new(), type_time: false, remaining_work: MAX_WORK, remaining_values: MAX_VALUES })
    }

    fn err(&self, id: NodeId, kind: impl Into<String>, message: String) -> HelixError {
        self.in_phase(self.graph.error(id, kind, message))
    }

    fn in_phase(&self, mut error: HelixError) -> HelixError {
        if self.type_time { error.phase = Some("type"); }
        error
    }

    /// Evaluate a node to plain YAML data. A node that needs its own value within one call is a `cycle`.
    pub fn eval(&mut self, id: NodeId) -> Result<Yaml, HelixError> {
        if self.remaining_work == 0 {
            return Err(self.err(id, "limit", format!("evaluation exceeds {MAX_WORK} node visits")));
        }
        self.remaining_work -= 1;
        if self.active.len() >= MAX_EVAL_DEPTH {
            return Err(self.err(id, "depth", format!("evaluation nested deeper than {MAX_EVAL_DEPTH} nodes")));
        }
        let local = self.in_function(id);
        if let Some(v) = self.memo.get(&id) {
            let value = v.clone();
            self.charge_value(id, &value)?;
            return Ok(value);
        }
        let key = (id, if local { self.frames.len() } else { 0 });
        if !self.active.insert(key) {
            return Err(self.err(id, "cycle", "value depends on itself".into()));
        }
        let result = self.eval_uncached(id).and_then(|value| {
            self.charge_value(id, &value)?;
            Ok(value)
        });
        let result = result.map_err(|error| self.in_phase(error));
        self.active.remove(&key);
        if let (false, Ok(v)) = (local, &result) {
            self.memo.insert(id, v.clone());
        }
        result
    }

    /// Bound cumulative produced data, including strings and nested collection copies.
    fn charge_value(&mut self, id: NodeId, value: &Yaml) -> Result<(), HelixError> {
        let mut pending = vec![(value, 0)];
        while let Some((value, depth)) = pending.pop() {
            if depth > crate::graph::MAX_NESTING {
                return Err(self.err(id, "depth", format!("value nested deeper than {} containers", crate::graph::MAX_NESTING)));
            }
            let size = match value { Yaml::String(s) | Yaml::Real(s) => 1 + s.len(), _ => 1 };
            self.remaining_values = self.remaining_values.checked_sub(size)
                .ok_or_else(|| self.err(id, "limit", format!("evaluation exceeds {MAX_VALUES} produced value units")))?;
            match value {
                Yaml::Array(items) => pending.extend(items.iter().map(|v| (v, depth + 1))),
                Yaml::Hash(entries) => pending.extend(entries.iter().flat_map(|(k, v)| [(k, depth + 1), (v, depth + 1)])),
                _ => {}
            }
        }
        Ok(())
    }

    /// Completed root fields become literal values; unfinished fields retain their source.
    /// Do not splice cached subexpressions into calls: argument positions interpret strings as symbols.
    pub(crate) fn recovery_source(&self) -> String {
        let value = match self.graph.kind(ROOT) {
            Kind::Map(entries) => Yaml::Hash(entries.iter().map(|(name, id)| {
                (s(name), self.memo.get(id).cloned().unwrap_or_else(|| self.raw(*id)))
            }).collect()),
            _ => self.memo.get(&ROOT).cloned().unwrap_or_else(|| self.raw(ROOT)),
        };
        emit::document(&value)
    }

    /// Every error in the program, not just the first: containers and non-expression lists are
    /// walked so each entry fails or succeeds on its own, and an expression is evaluated whole.
    /// Duplicates (one failure reached through several dependents) are reported once.
    pub(crate) fn collect_errors(&mut self) -> Vec<HelixError> {
        let mut errors: Vec<HelixError> = Vec::new();
        self.collect(ROOT, &mut errors);
        let mut seen = HashSet::new();
        errors.retain(|e| seen.insert((e.path.clone(), e.kind.clone(), e.message.clone())));
        errors
    }

    fn collect(&mut self, id: NodeId, errors: &mut Vec<HelixError>) {
        let children = match self.graph.kind(id) {
            Kind::Map(entries) if !self.in_function(id) => Some(entries.iter().map(|(_, child)| *child).collect::<Vec<_>>()),
            Kind::Seq(items) if !self.in_function(id) => Some(items.clone()),
            _ => None,
        };
        let children = if matches!(self.graph.kind(id), Kind::Seq(_)) {
            let items = children.clone().unwrap_or_default();
            match self.actor(&items) {
                Ok(None) => children,
                _ => None,
            }
        } else { children };
        match children {
            Some(children) => children.into_iter().for_each(|child| self.collect(child, errors)),
            None => errors.extend(self.eval(id).err()),
        }
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
            Kind::Seq(items) => Shape::Seq(items.clone(), None),
        };
        let shape = match shape {
            Shape::Seq(items, _) => {
                let actor = self.actor(&items)?;
                Shape::Seq(items, actor)
            }
            other => other,
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

    /// A sequence is an expression iff its head names a builtin or a user function.
    fn actor(&mut self, items: &[NodeId]) -> Result<Option<Actor>, HelixError> {
        let Some(&head) = items.first() else { return Ok(None) };
        let Some(name) = self.graph.symbol(head) else { return Ok(None) };
        if builtins::arity(name).is_some() {
            return Ok(Some(Actor::Builtin(name.to_string())));
        }
        self.function(head).map(|function| function.map(Actor::Function))
    }

    /// A function designator may be source, a lexical binding, or an expression producing a value.
    fn function(&mut self, id: NodeId) -> Result<Option<NodeId>, HelixError> {
        let target = match self.graph.symbol(id) {
            Some(name) => match self.resolve(id, name) {
                Some(Binding::Node(target)) => target,
                Some(Binding::Value(value)) => return self.function_value(&value, id),
                None => return Ok(None),
            },
            None => id,
        };
        if self.graph.form(target) == Some("fn") && matches!(self.graph.kind(target), Kind::Seq(items) if matches!(items.len(), 3 | 4)) {
            return Ok(Some(target));
        }
        if self.graph.form(target) == Some("closure") {
            let Kind::Seq(parts) = self.graph.kind(target) else { unreachable!() };
            if parts.len() == 3 && matches!(self.graph.kind(parts[1]), Kind::Map(_)) && self.graph.form(parts[2]) == Some("fn") {
                return Ok(Some(parts[2]));
            }
        }
        let value = self.eval(target)?;
        self.function_value(&value, id)
    }

    /// Reconstitute persisted function source with its captured lexical bindings.
    fn function_value(&mut self, value: &Yaml, blame: NodeId) -> Result<Option<NodeId>, HelixError> {
        if !builtins::is_function(value) {
            return Ok(None);
        }
        let source = emit::document(value);
        let root = match self.grafts.get(&source) {
            Some(&root) => root,
            None => {
                let parsed = Graph::parse(&source).map_err(|error| self.err(blame, "type", error.message))?;
                let root = self.graph.graft(parsed)?;
                self.grafts.insert(source, root);
                root
            }
        };
        if self.graph.form(root) == Some("closure") {
            let Kind::Seq(parts) = self.graph.kind(root) else { unreachable!() };
            if parts.len() != 3 || !matches!(self.graph.kind(parts[1]), Kind::Map(_)) || self.graph.form(parts[2]) != Some("fn") {
                return Err(self.err(blame, "type", "a closure needs bindings and a function".into()));
            }
            return Ok(Some(parts[2]));
        }
        Ok(Some(root))
    }

    /// Freeze only free lexical bindings. Function source remains readable; captured values live
    /// in a `closure` binding map, which is also the scope used after serialization and reloading.
    fn close(&mut self, function: NodeId) -> Result<Yaml, HelixError> {
        if self.closing.len() >= MAX_DEPTH {
            return Err(self.err(function, "depth", format!("closures nested deeper than {MAX_DEPTH}")));
        }
        if !self.closing.insert(function) {
            return self.capture(function, true);
        }
        let result = self.capture(function, false);
        self.closing.remove(&function);
        result
    }

    fn capture(&mut self, function: NodeId, recursive: bool) -> Result<Yaml, HelixError> {
        self.parameters(function)?;
        let mut bindings = Hash::new();
        let mut pending = vec![(function, HashSet::<String>::new(), true)];
        while let Some((node, mut locals, symbolic)) = pending.pop() {
            match self.graph.kind(node) {
                Kind::Seq(items) if self.graph.form(node) == Some("fn") => {
                    for (name, annotation) in self.parameters(node)? {
                        if let Some(annotation) = annotation { pending.push((annotation, locals.clone(), true)); }
                        locals.insert(name.to_string());
                    }
                    pending.push((items[2], locals, true));
                    for &annotation in items.get(3..).unwrap_or_default() {
                        pending.push((annotation, HashSet::new(), true));
                    }
                }
                Kind::Seq(items) => {
                    let head = items.first().and_then(|&head| self.graph.symbol(head));
                    let builtin = head.is_some_and(|name| builtins::arity(name).is_some());
                    let expression = builtin || head.is_some_and(|name| locals.contains(name) || self.resolve(node, name).is_some());
                    pending.extend(items[usize::from(builtin)..].iter().rev().map(|&child| (child, locals.clone(), expression)));
                }
                Kind::Map(entries) => pending.extend(entries.iter().rev().map(|(_, child)| (*child, locals.clone(), false))),
                Kind::Scalar { .. } => {
                    if !symbolic { continue; }
                    let Some(name) = self.graph.symbol(node).map(str::to_string) else { continue };
                    if locals.contains(&name) || bindings.contains_key(&s(&name)) { continue; }
                    let Some(binding) = self.resolve(node, &name) else { continue };
                    let value = match binding {
                        Binding::Value(value) => value,
                        Binding::Node(target) => {
                            let mut ancestor = Some(target);
                            while ancestor.is_some_and(|id| id != function) { ancestor = self.graph.parent(ancestor.unwrap()); }
                            if ancestor == Some(function) && target != function { continue; }
                            if self.graph.form(target) == Some("fn") {
                                if recursive && self.closing.contains(&target) { continue; }
                                self.close(target)?
                            } else { self.eval(target)? }
                        }
                    };
                    bindings.insert(s(&name), value);
                }
            }
        }
        let source = self.raw(function);
        if bindings.is_empty() { return Ok(source); }
        Ok(Yaml::Array(vec![Yaml::Real("closure".into()), Yaml::Hash(bindings), source]))
    }

    /// Whether `id` sits inside some `fn` form, so its value depends on a call.
    fn in_function(&self, id: NodeId) -> bool {
        let mut cur = self.graph.parent(id);
        while let Some(parent) = cur {
            if self.graph.form(parent) == Some("fn") {
                return true;
            }
            cur = self.graph.parent(parent);
        }
        false
    }

    /// The parameters of a `fn` form: each a name, or a `[name, type]` pair whose type node is
    /// checked against the argument on every call.
    fn parameters(&self, function: NodeId) -> Result<Vec<(&str, Option<NodeId>)>, HelixError> {
        let Kind::Seq(items) = self.graph.kind(function) else { unreachable!("a function is a sequence") };
        if !matches!(items.len(), 3 | 4) {
            return Err(self.err(function, "arity", format!("`fn` takes 2 to 3 arguments, got {}", items.len().saturating_sub(1))));
        }
        let malformed = || self.err(items[1], "type", "parameters must be a list of names or `[name, type]` pairs".into());
        let Kind::Seq(params) = self.graph.kind(items[1]) else { return Err(malformed()) };
        params
            .iter()
            .map(|&p| match self.graph.kind(p) {
                Kind::Seq(pair) if pair.len() == 2 => Ok((self.graph.symbol(pair[0]).ok_or_else(malformed)?, Some(pair[1]))),
                _ => Ok((self.graph.symbol(p).ok_or_else(malformed)?, None)),
            })
            .collect()
    }

    /// Resolve `name` from `from`. A dotted name that is not itself bound is a path: its first part
    /// resolves lexically and each further part selects a map entry, entering an imported module
    /// where the path reaches an `[import, ...]` form (`math.square`, `config.port`).
    fn resolve(&self, from: NodeId, name: &str) -> Option<Binding> {
        if let Some(found) = self.resolve_name(from, name) {
            return Some(found);
        }
        let (head, rest) = name.split_once('.')?;
        let mut binding = self.resolve_name(from, head)?;
        for part in rest.split('.') {
            binding = match binding {
                Binding::Node(node) => Binding::Node(self.member(node, part)?),
                Binding::Value(Yaml::Hash(map)) => Binding::Value(map.get(&s(part))?.clone()),
                _ => return None,
            };
        }
        Some(binding)
    }

    /// The entry `key` of the map at `id`, or of the module an import form at `id` loads.
    fn member(&self, id: NodeId, key: &str) -> Option<NodeId> {
        let id = self.modules.root_of(id).unwrap_or(id);
        let Kind::Map(entries) = self.graph.kind(id) else { return None };
        entries.iter().find(|(k, _)| k == key).map(|(_, target)| *target)
    }

    /// Lexical resolution from `from`: the nearest enclosing map entry, parameter of an enclosing
    /// `fn` (read from its innermost active call), or binding of an enclosing `let` named `name`.
    fn resolve_name(&self, from: NodeId, name: &str) -> Option<Binding> {
        let entry = |entries: &[(String, NodeId)]| entries.iter().find(|(k, _)| k == name).map(|(_, target)| Binding::Node(*target));
        let mut child = from;
        while let Some(id) = self.graph.parent(child) {
            let found = match self.graph.kind(id) {
                Kind::Map(entries) => entry(entries),
                // Parameters and `let` bindings are in scope only in the body, the third item.
                Kind::Seq(items) if items.get(2) == Some(&child) => match (self.graph.form(id), self.graph.kind(items[1])) {
                    (Some("fn"), _) => self.parameters(id).ok().and_then(|params| params.iter().position(|(p, _)| *p == name)).and_then(|i| {
                        let frame = self.frames.iter().rev().find(|f| f.function == id)?;
                        Some(Binding::Value(frame.args[i].clone()))
                    }),
                    (Some("let" | "closure"), Kind::Map(entries)) => entry(entries),
                    _ => None,
                },
                _ => None,
            };
            if found.is_some() {
                return found;
            }
            child = id;
        }
        // Code grafted in as data (a refinement's predicate) is detached; it sees the document root.
        // A module is detached too, but its names are its own.
        match self.graph.kind(ROOT) {
            Kind::Map(entries) if child != ROOT && !self.modules.is_root(child) && !self.grafts.values().any(|&root| root == child) => entry(entries),
            _ => None,
        }
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
            Actor::Builtin(name) => builtins::apply(&name, &values, &mut |check, value| self.predicate(check, value)).map_err(|fault| self.from_fault(id, args, fault)),
        }
    }

    /// Explicit pure region. Budgets are shared with the caller, never reset by nesting.
    fn at_type_time(&mut self, expression: NodeId) -> Result<Yaml, HelixError> {
        let previous = self.type_time;
        self.type_time = true;
        // Recompute dependencies in this phase, including caught errors stored as values.
        let memo = if previous { None } else { Some(std::mem::take(&mut self.memo)) };
        let result = match self.modules.file_import() {
            Some(import) => Err(self.err(import, "effect", "file import is not allowed in type time; use embedded std modules or supply source data directly".into())),
            None => self.value_arg(expression),
        };
        if let Some(memo) = memo { self.memo = memo; }
        self.type_time = previous;
        result
    }

    /// A builtin `Fault` as an error blamed on the offending argument node, or the whole call.
    fn from_fault(&self, site: NodeId, args: &[NodeId], fault: Fault) -> HelixError {
        HelixError { cause: fault.cause, data: fault.data, ..self.err(fault.arg.and_then(|i| args.get(i).copied()).unwrap_or(site), fault.kind, fault.message) }
    }

    /// Run a refinement's predicate source on `value`. The source arrives as data, so it is grafted
    /// into the graph (once per distinct source) and called like any function.
    fn predicate(&mut self, check: &Yaml, value: &Yaml) -> Result<bool, String> {
        let source = emit::document(check);
        let id = match self.grafts.get(&source) {
            Some(&id) => id,
            None => {
                let id = self.graph.graft(Graph::parse(&source).map_err(|e| e.message)?).map_err(|e| e.message)?;
                self.grafts.insert(source, id);
                id
            }
        };
        let function = self.function(id).map_err(|error| error.message)?.ok_or("a refinement check must be `[fn, [value], body]`")?;
        if self.parameters(function).map_err(|e| e.message)?.len() != 1 {
            return Err("a refinement check takes exactly 1 parameter".into());
        }
        match self.call(id, function, vec![value.clone()]) {
            Ok(Yaml::Boolean(verdict)) => Ok(verdict),
            Ok(other) => Err(format!("returned {}, expected boolean", builtins::type_of(&other))),
            Err(e) => Err(format!("{}: {}", e.kind, e.message)),
        }
    }

    /// Where `value` does not fit `ty`; `blame` is the node a malformed `ty` is attributed to.
    fn misfits(&mut self, value: &Yaml, ty: &Yaml, blame: NodeId) -> Result<Vec<builtins::Violation>, HelixError> {
        builtins::violations(value, ty, &mut |check, v| self.predicate(check, v)).map_err(|why| self.err(blame, "type", why))
    }

    /// The `type` error for violations found in `what`, carrying every one as `data`.
    fn violation_error(&self, blame: NodeId, what: &str, found: Vec<Yaml>) -> HelixError {
        let first = found.iter().find_map(|v| if let Yaml::Hash(h) = v { h.get(&s("message")).and_then(|m| m.as_str().map(str::to_string)) } else { None }).unwrap_or_default();
        let plural = if found.len() == 1 { "" } else { "s" };
        let mut error = self.err(blame, "type", format!("{} type violation{plural} in {what}: {first}", found.len()));
        let mut data = Hash::new();
        data.insert(s("violations"), Yaml::Array(found));
        error.data = Some(Yaml::Hash(data));
        error
    }

    /// Run a function body with `args` bound to its parameters; `site` is blamed for a wrong
    /// argument count or a call nested deeper than `MAX_DEPTH`.
    fn call(&mut self, site: NodeId, function: NodeId, args: Vec<Yaml>) -> Result<Yaml, HelixError> {
        self.call_body(site, function, args).map_err(|mut error| {
            let mut location = Hash::new();
            if let Some(file) = self.graph.source(site) { location.insert(s("file"), s(file)); }
            location.insert(s("path"), s(&self.graph.path(site)));
            let span = self.graph.span(site);
            location.insert(s("line"), Yaml::Integer(span.line as i64));
            location.insert(s("column"), Yaml::Integer(span.col as i64));
            location.insert(s("function"), s(&self.graph.path(function)));
            if let Some(file) = self.graph.source(function) { location.insert(s("function_file"), s(file)); }
            let definition = self.graph.span(function);
            location.insert(s("function_line"), Yaml::Integer(definition.line as i64));
            location.insert(s("function_column"), Yaml::Integer(definition.col as i64));
            error.calls.push(Yaml::Hash(location));
            error
        })
    }

    fn call_body(&mut self, site: NodeId, function: NodeId, args: Vec<Yaml>) -> Result<Yaml, HelixError> {
        let params: Vec<(String, Option<NodeId>)> = self.parameters(function)?.into_iter().map(|(name, ty)| (name.to_string(), ty)).collect();
        let n = params.len();
        if n != args.len() {
            let message = format!("function `{}` takes {}, got {}", self.graph.path(function), arguments(n), args.len());
            return Err(self.err(site, "arity", message));
        }
        if self.frames.len() >= MAX_DEPTH {
            return Err(self.err(site, "depth", format!("calls nested deeper than {MAX_DEPTH}")));
        }
        let Kind::Seq(items) = self.graph.kind(function) else { unreachable!("a function is a sequence") };
        let (body, result_type) = (items[2], items.get(3).copied());
        let mut found = Vec::new();
        for ((name, ty), value) in params.iter().zip(&args) {
            let Some(ty) = *ty else { continue };
            let declared = self.value_arg(ty)?;
            for violation in self.misfits(value, &declared, ty)? {
                found.push(tagged("argument", name, violation.to_yaml()));
            }
        }
        if !found.is_empty() {
            return Err(self.violation_error(site, &format!("the arguments of `{}`", self.graph.path(function)), found));
        }
        self.frames.push(Frame { function, args });
        let result = self.value_arg(body);
        self.frames.pop();
        let result = result?;
        if let Some(ty) = result_type {
            let declared = self.value_arg(ty)?;
            let found: Vec<Yaml> = self.misfits(&result, &declared, ty)?.iter().map(builtins::Violation::to_yaml).collect();
            if !found.is_empty() {
                return Err(self.violation_error(body, "the result", found));
            }
        }
        Ok(result)
    }

    /// Call the function designated by `designator` (an inline `fn` or a name) with `values`.
    fn call_arg(&mut self, designator: NodeId, values: Vec<Yaml>) -> Result<Yaml, HelixError> {
        let function = self.function(designator)?.ok_or_else(|| self.err(designator, "type", "expected a function (`[fn, [params], body]` or its name)".into()))?;
        self.call(designator, function, values)
    }

    /// Forms with their own evaluation order: `if`, `cond`, `and`, `or` evaluate only what they
    /// need; `try` hands an error from its first argument to its handler as a map; `fn` yields its own source; `let` evaluates its body with the bindings in scope; the
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
            "call" => {
                let values = args[1..].iter().map(|&arg| self.value_arg(arg)).collect::<Result<_, _>>()?;
                self.call_arg(args[0], values)
            }
            "closure" => {
                if !matches!(self.graph.kind(args[0]), Kind::Map(_)) || self.graph.form(args[1]) != Some("fn") {
                    return Err(self.err(id, "type", "a closure needs bindings and a function".into()));
                }
                Ok(self.raw(id))
            }
            "fn" => {
                self.parameters(id)?;
                let mut parent = self.graph.parent(id);
                while let Some(node) = parent {
                    if matches!(self.graph.form(node), Some("fn" | "let")) { return self.close(id); }
                    parent = self.graph.parent(node);
                }
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
            "refine" => {
                let base = self.value_arg(args[0])?;
                let function = self.function(args[1])?.ok_or_else(|| self.err(args[1], "type", "expected a function (`[fn, [value], body]` or its name)".into()))?;
                if self.parameters(function)?.len() != 1 {
                    return Err(self.err(args[1], "arity", "a refinement check takes exactly 1 parameter".into()));
                }
                builtins::refine(base, self.value_arg(args[1])?).map_err(|fault| self.from_fault(id, args, fault))
            }
            "type_time" => self.at_type_time(args[0]),
            "import" => match self.modules.root_of(id) {
                Some(root) => self.eval(root),
                None => Err(self.err(id, "import", "an import must be a literal `[import, SPEC]` in the program or a module it imports".into())),
            },
            "finally" => {
                let result = self.value_arg(args[0]);
                self.value_arg(args[1])?;
                result
            }
            "try" => match self.value_arg(args[0]) {
                Ok(value) => Ok(value),
                Err(error) => self.call_arg(args[1], vec![error.to_yaml()]),
            },
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

    /// Evaluate an expression argument: plain strings are symbols; functions capture their free bindings.
    fn value_arg(&mut self, id: NodeId) -> Result<Yaml, HelixError> {
        let target = match self.graph.symbol(id) {
            Some(name) => match self.resolve(id, name) {
                Some(Binding::Node(target)) => target,
                Some(Binding::Value(value)) => {
                    self.charge_value(id, &value)?;
                    return Ok(value);
                }
                None if builtins::is_primitive(name) => return Ok(Yaml::String(name.to_string())),
                None => return Err(self.err(id, "unbound", format!("unbound symbol `{name}`"))),
            },
            None => id,
        };
        self.argument_value(target)
    }

    /// Collections passed as values retain the lexical scope of every nested function.
    /// Plain scalars within a collection remain data, not expression arguments.
    fn argument_value(&mut self, id: NodeId) -> Result<Yaml, HelixError> {
        if self.graph.form(id) == Some("fn") {
            return self.close(id);
        }
        match self.graph.kind(id) {
            Kind::Map(entries) => {
                let entries = entries.clone();
                let mut values = Hash::new();
                for (name, child) in entries {
                    values.insert(s(&name), self.argument_value(child)?);
                }
                Ok(Yaml::Hash(values))
            }
            Kind::Seq(items) => {
                let items = items.clone();
                if self.actor(&items)?.is_some() {
                    self.eval(id)
                } else {
                    Ok(Yaml::Array(items.into_iter().map(|child| self.argument_value(child)).collect::<Result<_, _>>()?))
                }
            }
            _ => self.eval(id),
        }
    }
}

/// `1 argument`, `2 arguments`.
fn arguments(n: usize) -> String {
    if n == 1 { "1 argument".into() } else { format!("{n} arguments") }
}

/// A violation map led by the name of what it was found in: `{argument: n, at: ..., message: ...}`.
fn tagged(label: &str, name: &str, violation: Yaml) -> Yaml {
    let mut tagged = Hash::new();
    tagged.insert(s(label), s(name));
    if let Yaml::Hash(fields) = violation {
        tagged.extend(fields);
    }
    Yaml::Hash(tagged)
}
