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
use crate::error::HelixError;
use crate::graph::{Graph, Kind, NodeId, ROOT};
use crate::link::{self, Modules};
use crate::source;
use crate::value;

/// Nested user-function calls allowed before failing with a `depth` error.
const MAX_DEPTH: usize = 200;
/// Iterations `until` allows before failing with a `limit` error.
const MAX_STEPS: usize = 100_000;
const MAX_WORK: usize = 2_000_000;
const MAX_VALUES: usize = 128_000_000;
const MAX_VALUE_SIZE: usize = 1_000_000;
const MAX_EVAL_DEPTH: usize = 512;

/// What a symbol resolves to: a node to evaluate, or an argument value of an active call.
enum Binding {
    Node(NodeId),
    Value(Yaml),
}

/// What the head of a vector form names.
enum Actor {
    Builtin(&'static builtins::Descriptor),
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
    /// Callable and predicate sources reconstituted in this evaluator's graph.
    grafts: source::Grafts,
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
        Ok(Evaluator { graph, memo: HashMap::new(), active: HashSet::new(), frames: Vec::new(), grafts: source::Grafts::default(), modules, closing: HashSet::new(), type_time: false, remaining_work: MAX_WORK, remaining_values: MAX_VALUES })
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
        let mut pending = vec![(value, 0, true)];
        let mut produced = 0;
        let mut nodes = 0;
        while let Some((value, depth, graph_node)) = pending.pop() {
            if depth > crate::graph::MAX_NESTING {
                return Err(self.err(id, "depth", format!("value nested deeper than {} containers", crate::graph::MAX_NESTING)));
            }
            nodes += usize::from(graph_node);
            if nodes > crate::graph::MAX_NODES {
                return Err(self.err(id, "limit", format!("value exceeds {} nodes", crate::graph::MAX_NODES)));
            }
            let size = match value { Yaml::String(s) | Yaml::Real(s) => 1 + s.len(), _ => 1 };
            produced += size;
            if produced > MAX_VALUE_SIZE {
                return Err(self.err(id, "limit", format!("value exceeds {MAX_VALUE_SIZE} units")));
            }
            self.remaining_values = self.remaining_values.checked_sub(size)
                .ok_or_else(|| self.err(id, "limit", format!("evaluation exceeds {MAX_VALUES} produced value units")))?;
            match value {
                Yaml::Array(items) => pending.extend(items.iter().map(|v| (v, depth + 1, true))),
                Yaml::Hash(entries) => pending.extend(entries.iter().flat_map(|(k, v)| [(k, depth + 1, false), (v, depth + 1, true)])),
                _ => {}
            }
        }
        Ok(())
    }

    /// Completed root fields become literal values; unfinished fields retain their source.
    /// Do not splice cached subexpressions into calls: argument positions interpret strings as symbols.
    pub(crate) fn recovery_source(&self) -> String {
        source::recovery(&self.graph, &self.memo)
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
        if let Some(actor) = builtins::descriptor(name) {
            return Ok(Some(Actor::Builtin(actor)));
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
        if !value::is_function(value) {
            return Ok(None);
        }
        let root = self.grafts.load(&mut self.graph, value).map_err(|error| match error {
            source::LoadError::Parse(error) => self.err(blame, "type", error.message),
            source::LoadError::Graft(error) => error,
        })?;
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
                    let builtin = head.is_some_and(|name| builtins::descriptor(name).is_some());
                    let expression = builtin || head.is_some_and(|name| locals.contains(name) || self.resolve(node, name).is_some());
                    pending.extend(items[usize::from(builtin)..].iter().rev().map(|&child| (child, locals.clone(), expression)));
                }
                Kind::Map(entries) => pending.extend(entries.iter().rev().map(|(_, child)| (*child, locals.clone(), false))),
                Kind::Scalar { .. } => {
                    if !symbolic { continue; }
                    let Some(name) = self.graph.symbol(node).map(str::to_string) else { continue };
                    if locals.contains(&name) || bindings.contains_key(&Yaml::String(name.to_string())) { continue; }
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
                    bindings.insert(Yaml::String(name.to_string()), value);
                }
            }
        }
        let source = source::subtree(&self.graph, function);
        Ok(source::closure(source, bindings))
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
                Binding::Value(Yaml::Hash(map)) => Binding::Value(map.get(&Yaml::String(part.to_string()))?.clone()),
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
            Kind::Map(entries) if child != ROOT && !self.modules.is_root(child) && !self.grafts.is_root(child) => entry(entries),
            _ => None,
        }
    }

    fn apply(&mut self, id: NodeId, actor: Actor, args: &[NodeId]) -> Result<Yaml, HelixError> {
        if let Actor::Builtin(descriptor) = &actor {
            let name = descriptor.name;
            let (min, max) = descriptor.arity;
            if args.len() < min || max.is_some_and(|m| args.len() > m) {
                let expected = match max {
                    Some(m) if m == min => arguments(m),
                    Some(m) => format!("{min} to {}", arguments(m)),
                    None => format!("at least {}", arguments(min)),
                };
                return Err(self.err(id, "arity", format!("`{name}` takes {expected}, got {}", args.len())));
            }
            if let builtins::Evaluation::Form(form) = descriptor.evaluation {
                return self.apply_form(id, form, args);
            }
        }
        let values = args.iter().map(|&arg| self.value_arg(arg)).collect::<Result<Vec<_>, _>>()?;
        match actor {
            Actor::Function(function) => self.call(id, function, values),
            Actor::Builtin(descriptor) => {
                builtins::apply(descriptor, &values, &mut |check, value| self.predicate(check, value)).map_err(|fault| self.from_fault(id, args, fault))
            }
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
        let id = self.grafts.load(&mut self.graph, check).map_err(|error| error.error().message)?;
        let function = self.function(id).map_err(|error| error.message)?.ok_or("a refinement check must be `[fn, [value], body]`")?;
        if self.parameters(function).map_err(|e| e.message)?.len() != 1 {
            return Err("a refinement check takes exactly 1 parameter".into());
        }
        match self.call(id, function, vec![value.clone()]) {
            Ok(Yaml::Boolean(verdict)) => Ok(verdict),
            Ok(other) => Err(format!("returned {}, expected boolean", value::type_name(&other))),
            Err(e) => Err(format!("{}: {}", e.kind, e.message)),
        }
    }

    /// Where `value` does not fit `ty`; `blame` is the node a malformed `ty` is attributed to.
    fn misfits(&mut self, value: &Yaml, ty: &Yaml, blame: NodeId) -> Result<Vec<builtins::Violation>, HelixError> {
        builtins::violations(value, ty, &mut |check, v| self.predicate(check, v)).map_err(|why| self.err(blame, "type", why))
    }

    /// The `type` error for violations found in `what`, carrying every one as `data`.
    fn violation_error(&self, blame: NodeId, what: &str, found: Vec<Yaml>) -> HelixError {
        let first = found.iter().find_map(|v| if let Yaml::Hash(h) = v { h.get(&Yaml::String("message".to_string())).and_then(|m| m.as_str().map(str::to_string)) } else { None }).unwrap_or_default();
        let plural = if found.len() == 1 { "" } else { "s" };
        let mut error = self.err(blame, "type", format!("{} type violation{plural} in {what}: {first}", found.len()));
        let mut data = Hash::new();
        data.insert(Yaml::String("violations".to_string()), Yaml::Array(found));
        error.data = Some(Yaml::Hash(data));
        error
    }

    /// Run a function body with `args` bound to its parameters; `site` is blamed for a wrong
    /// argument count or a call nested deeper than `MAX_DEPTH`.
    fn call(&mut self, site: NodeId, function: NodeId, args: Vec<Yaml>) -> Result<Yaml, HelixError> {
        self.call_body(site, function, args).map_err(|mut error| {
            let mut location = Hash::new();
            if let Some(file) = self.graph.source(site) { location.insert(Yaml::String("file".to_string()), Yaml::String(file.to_string())); }
            location.insert(Yaml::String("path".to_string()), Yaml::String(self.graph.path(site).to_string()));
            let span = self.graph.span(site);
            location.insert(Yaml::String("line".to_string()), Yaml::Integer(span.line as i64));
            location.insert(Yaml::String("column".to_string()), Yaml::Integer(span.col as i64));
            location.insert(Yaml::String("function".to_string()), Yaml::String(self.graph.path(function).to_string()));
            if let Some(file) = self.graph.source(function) { location.insert(Yaml::String("function_file".to_string()), Yaml::String(file.to_string())); }
            let definition = self.graph.span(function);
            location.insert(Yaml::String("function_line".to_string()), Yaml::Integer(definition.line as i64));
            location.insert(Yaml::String("function_column".to_string()), Yaml::Integer(definition.col as i64));
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
    fn apply_form(&mut self, id: NodeId, actor: builtins::Form, args: &[NodeId]) -> Result<Yaml, HelixError> {
        use builtins::Form::{And, Call, Closure, Cond, Each, Filter, Finally, Fn, Fold, If, Import, Let, Map, Or, Refine, Try, TypeTime, Until};
        match actor {
            If => {
                let condition = self.condition(args[0])?;
                self.value_arg(args[if condition { 1 } else { 2 }])
            }
            Cond => {
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
            And | Or => {
                let stop_on = actor == Or;
                for &arg in args {
                    if self.condition(arg)? == stop_on {
                        return Ok(Yaml::Boolean(stop_on));
                    }
                }
                Ok(Yaml::Boolean(!stop_on))
            }
            Call => {
                let values = args[1..].iter().map(|&arg| self.value_arg(arg)).collect::<Result<_, _>>()?;
                self.call_arg(args[0], values)
            }
            Closure => {
                if !matches!(self.graph.kind(args[0]), Kind::Map(_)) || self.graph.form(args[1]) != Some("fn") {
                    return Err(self.err(id, "type", "a closure needs bindings and a function".into()));
                }
                Ok(source::subtree(&self.graph, id))
            }
            Fn => {
                self.parameters(id)?;
                let mut parent = self.graph.parent(id);
                while let Some(node) = parent {
                    if matches!(self.graph.form(node), Some("fn" | "let")) { return self.close(id); }
                    parent = self.graph.parent(node);
                }
                Ok(source::subtree(&self.graph, id))
            }
            Let => {
                if !matches!(self.graph.kind(args[0]), Kind::Map(_)) {
                    return Err(self.err(args[0], "type", "`let` bindings must be a map".into()));
                }
                self.value_arg(args[1])
            }
            Map => self.list_arg(args[1])?.into_iter().map(|item| self.call_arg(args[0], vec![item])).collect::<Result<_, _>>().map(Yaml::Array),
            Filter => {
                let mut kept = Vec::new();
                for item in self.list_arg(args[1])? {
                    let keep = self.call_arg(args[0], vec![item.clone()])?;
                    if self.truth(&keep, args[0])? {
                        kept.push(item);
                    }
                }
                Ok(Yaml::Array(kept))
            }
            Fold => {
                let init = self.value_arg(args[1])?;
                self.list_arg(args[2])?.into_iter().try_fold(init, |acc, item| self.call_arg(args[0], vec![acc, item]))
            }
            Each => {
                for item in self.list_arg(args[1])? {
                    self.call_arg(args[0], vec![item])?;
                }
                Ok(Yaml::Null)
            }
            Until => {
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
            Refine => {
                let base = self.value_arg(args[0])?;
                let function = self.function(args[1])?.ok_or_else(|| self.err(args[1], "type", "expected a function (`[fn, [value], body]` or its name)".into()))?;
                if self.parameters(function)?.len() != 1 {
                    return Err(self.err(args[1], "arity", "a refinement check takes exactly 1 parameter".into()));
                }
                builtins::refine(base, self.value_arg(args[1])?).map_err(|fault| self.from_fault(id, args, fault))
            }
            TypeTime => self.at_type_time(args[0]),
            Import => match self.modules.root_of(id) {
                Some(root) => self.eval(root),
                None => Err(self.err(id, "import", "an import must be a literal `[import, SPEC]` in the program or a module it imports".into())),
            },
            Finally => {
                let result = self.value_arg(args[0]);
                self.value_arg(args[1])?;
                result
            }
            Try => match self.value_arg(args[0]) {
                Ok(value) => Ok(value),
                Err(error) => self.call_arg(args[1], vec![error.to_yaml()]),
            },
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
                    values.insert(Yaml::String(name.to_string()), self.argument_value(child)?);
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
    tagged.insert(Yaml::String(label.to_string()), Yaml::String(name.to_string()));
    if let Yaml::Hash(fields) = violation {
        tagged.extend(fields);
    }
    Yaml::Hash(tagged)
}
