//! Explicit execution requests and continuations. No transition invokes another interpreter.
use crate::builtins::{self, Fault};
use crate::error::{HelixError, s};
use crate::graph::{Graph, Kind, NodeId, ROOT};
use crate::link;
use crate::value::{Hash, Value};
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::Arc;

use crate::model::Limits;
impl Default for Limits {
    fn default() -> Self {
        Self {
            max_depth: 200,
            max_iterations: 100_000,
            max_work: 2_000_000,
            max_values: 128_000_000,
            max_value_size: 1_000_000,
            max_eval_depth: 512,
        }
    }
}
use crate::model::{Outcome, Vm};

use crate::model::{Actor, Atomic, Binding, Mode, Operation, Program, Request, Scope};
pub(crate) use crate::model::{Evaluator, Function};
impl Function {
    /// Free lexical bindings remain lazy code references. Recursive bindings form graph edges,
    /// not recursively expanded source or mutable environment cells.
    pub(crate) fn free_bindings(&self, node: NodeId) -> Vec<(String, Binding)> {
        let evaluator = Evaluator::start(
            self.program.clone(),
            self.environment.clone(),
            Request::Eval(node, Mode::Node),
        );
        let graph = &self.program.graph;
        let mut found = Vec::new();
        let mut pending = vec![(node, HashSet::<String>::new(), true)];
        while let Some((id, mut locals, symbolic)) = pending.pop() {
            match graph.kind(id) {
                Kind::Seq(items) if graph.form(id) == Some("fn") => {
                    let function = Function {
                        program: self.program.clone(),
                        node: id,
                        environment: Vec::new(),
                    };
                    if let Ok(params) = evaluator.parameters(&function) {
                        for (name, annotation) in params {
                            if let Some(ty) = annotation {
                                pending.push((ty, locals.clone(), true));
                            }
                            locals.insert(name);
                        }
                    }
                    if let Some(body) = items.get(2) {
                        pending.push((*body, locals, true));
                    }
                    if let Some(ty) = items.get(3) {
                        pending.push((*ty, HashSet::new(), true));
                    }
                }
                Kind::Seq(items) => {
                    let head = items.first().and_then(|head| graph.symbol(*head));
                    let builtin = head.is_some_and(|name| builtins::arity(name).is_some());
                    let expression = builtin
                        || head.is_some_and(|name| {
                            locals.contains(name) || evaluator.resolve(id, name).is_some()
                        })
                        || items
                            .first()
                            .is_some_and(|head| matches!(graph.kind(*head), Kind::Seq(_)));
                    pending.extend(
                        items[usize::from(builtin)..]
                            .iter()
                            .rev()
                            .map(|child| (*child, locals.clone(), expression)),
                    );
                }
                Kind::Map(entries) => pending.extend(
                    entries
                        .iter()
                        .rev()
                        .map(|(_, child)| (*child, locals.clone(), false)),
                ),
                Kind::Scalar { .. } if symbolic => {
                    let Some(name) = graph.symbol(id) else {
                        continue;
                    };
                    if locals.contains(name) {
                        continue;
                    }
                    let Some(binding) = evaluator.resolve(id, name) else {
                        continue;
                    };
                    if let Binding::Node(target) = binding {
                        let mut ancestor = Some(target);
                        while let Some(parent) = ancestor {
                            if parent == node {
                                break;
                            }
                            ancestor = graph.parent(parent);
                        }
                        if ancestor == Some(node) && target != node {
                            continue;
                        }
                    }
                    if !found.iter().any(|(key, _)| key == name) {
                        found.push((name.to_string(), binding));
                    }
                }
                _ => {}
            }
        }
        found
    }
}
impl Evaluator {
    pub fn new(
        mut graph: Graph,
        main: Option<&Path>,
        roots: Option<&[PathBuf]>,
    ) -> Result<Self, HelixError> {
        let modules = link::link(&mut graph, main, roots)?;
        Ok(Self::start(
            Arc::new(Program {
                code: graph.code_views(),
                graph,
                modules,
            }),
            Vec::new(),
            Request::Eval(ROOT, Mode::Node),
        ))
    }
    fn start(program: Arc<Program>, environment: Vec<Scope>, request: Request) -> Self {
        let limits = Limits::default();
        Self {
            program,
            environment,
            memo: HashMap::new(),
            active: HashSet::new(),
            requests: vec![request],
            answer: None,
            depth: 0,
            type_time: false,
            remaining_work: limits.max_work,
            remaining_values: limits.max_values,
            limits,
        }
    }
    pub fn child(function: Arc<Function>) -> Self {
        Self::start(
            function.program.clone(),
            function.environment.clone(),
            Request::Call(function.node, function, Vec::new()),
        )
    }
    fn graph(&self) -> &Graph {
        &self.program.graph
    }
    fn err(&self, id: NodeId, kind: &str, message: impl Into<String>) -> HelixError {
        let mut error = self.graph().error(id, kind, message.into());
        if self.type_time {
            error.phase = Some("type");
        }
        error
    }
    fn take(&mut self) -> Outcome {
        self.answer
            .take()
            .expect("a continuation receives a result")
    }
    fn value(&mut self) -> Value {
        self.take()
            .expect("errors unwind before ordinary continuations")
    }
    fn push(&mut self, request: Request) {
        self.requests.push(request);
    }
    fn eval(&mut self, node: NodeId, argument: bool) {
        self.push(Request::Eval(
            node,
            if argument { Mode::Argument } else { Mode::Node },
        ));
    }
    fn finish(&mut self, value: Value) {
        self.answer = Some(Ok(value));
    }
    fn fail(&mut self, mut error: HelixError) {
        if self.type_time {
            error.phase = Some("type");
        }
        self.answer = Some(Err(error));
    }
    pub fn take_child(&mut self) -> Option<Vm> {
        if !matches!(self.requests.last(), Some(Request::AdvanceChild)) {
            return None;
        }
        self.requests.pop();
        let Value::Machine(child) = self.value() else {
            unreachable!()
        };
        Some((*child).clone())
    }
    pub fn return_child(&mut self, child: Vm) {
        self.finish(Value::Machine(Arc::new(child)));
    }
    pub fn outcome(&self) -> Option<&Outcome> {
        if self.requests.is_empty() {
            self.answer.as_ref()
        } else {
            None
        }
    }

    /// One dispatcher transition, including nested child work, without recursive interpreter calls.
    pub fn transition(&mut self) {
        let Some(request) = self.requests.pop() else {
            return;
        };
        if matches!(self.answer, Some(Err(_)))
            && !matches!(
                request,
                Request::TypeEnd { .. }
                    | Request::Finish(..)
                    | Request::CallTrace(..)
                    | Request::CallEnd { .. }
                    | Request::Try(..)
                    | Request::Finally(..)
                    | Request::Cleanup(..)
                    | Request::PredicateEnd(..)
            )
        {
            return;
        }
        if let Err(error) = self.dispatch(request) {
            self.fail(error);
        }
    }
    fn dispatch(&mut self, request: Request) -> Result<(), HelixError> {
        match request {
            Request::TypeEnd { previous, memo } => {
                self.type_time = previous;
                if let Some(memo) = memo {
                    self.memo = memo;
                }
            }
            Request::Eval(id, mode) => self.evaluate(id, mode)?,
            Request::Finish(id, depth, local) => {
                self.active.remove(&(id, depth));
                if let Some(Ok(value)) = &self.answer {
                    let value = value.clone();
                    self.charge_value(id, &value)?;
                }
                if !local {
                    if let Some(Ok(value)) = &self.answer {
                        self.memo.insert(id, value.clone());
                    }
                }
            }
            Request::Actor(id, items) => {
                let value = self.value();
                if let Value::Function(function) = value {
                    self.apply(id, Actor::Function(function), items[1..].to_vec())?;
                } else {
                    self.collect(id, None, items, None, false);
                }
            }
            Request::Collect {
                site,
                actor,
                nodes,
                keys,
                mut values,
                argument,
            } => {
                values.push(self.value());
                self.collect_next(site, actor, nodes, keys, values, argument);
            }
            Request::Apply(site, actor, nodes, values) => match actor {
                Actor::Function(function) => self.push(Request::Call(site, function, values)),
                Actor::Builtin(name) if name == "step" => {
                    let Value::Machine(child) = &values[0] else {
                        return Err(self.err(nodes[0], "type", "expected a vm"));
                    };
                    // Child dispatch is flattened by the outer VM runner (see advance_child).
                    self.finish(Value::Machine(child.clone()));
                    self.push(Request::AdvanceChild);
                }
                Actor::Builtin(name) if name == "inspect" => {
                    let Value::Machine(child) = &values[0] else {
                        return Err(self.err(nodes[0], "type", "expected a vm"));
                    };
                    self.finish(crate::inspect::machine(child));
                }
                Actor::Builtin(name) => self.push(Request::Atomic(Atomic {
                    site,
                    nodes,
                    operation: Operation::Builtin(name, values),
                    answers: Vec::new(),
                })),
            },
            Request::Call(site, function, args) => {
                self.push(Request::CallTrace(site, function.clone()));
                let params = self.parameters(&function)?;
                if params.len() != args.len() {
                    return Err(self.err(
                        site,
                        "arity",
                        format!(
                            "function `{}` takes {}, got {}",
                            function.program.graph.path(function.node),
                            arguments(params.len()),
                            args.len()
                        ),
                    ));
                }
                if self.depth >= self.limits.max_depth {
                    return Err(self.err(
                        site,
                        "depth",
                        format!("calls nested deeper than {}", self.limits.max_depth),
                    ));
                }
                let environment =
                    std::mem::replace(&mut self.environment, function.environment.clone());
                let program = std::mem::replace(&mut self.program, function.program.clone());
                self.depth += 1;
                self.push(Request::CallEnd {
                    environment,
                    program,
                });
                self.push(Request::Parameters {
                    site,
                    function,
                    args,
                    index: 0,
                    found: Vec::new(),
                });
            }
            Request::CallArgument(designator, args) => {
                self.push(Request::CallResolved(designator, args));
                self.eval(designator, true);
            }
            Request::CallResolved(site, args) => {
                let Value::Function(function) = self.value() else {
                    return Err(self.err(
                        site,
                        "type",
                        "expected a function (`[fn, [params], body]` or its name)",
                    ));
                };
                self.push(Request::Call(site, function, args));
            }
            Request::Parameters {
                site,
                function,
                args,
                index,
                found,
            } => self.parameters_next(site, function, args, index, found)?,
            Request::ParameterType {
                site,
                function,
                args,
                index,
                found,
                annotation,
            } => {
                let ty = self.value();
                let value = args[index].clone();
                self.push(Request::ParameterChecked {
                    site,
                    function,
                    args,
                    index,
                    found,
                });
                self.push(Request::Atomic(Atomic {
                    site: annotation,
                    nodes: Vec::new(),
                    operation: Operation::Violations(value, ty),
                    answers: Vec::new(),
                }));
            }
            Request::ParameterChecked {
                site,
                function,
                args,
                index,
                mut found,
            } => {
                let Value::Array(violations) = self.value() else {
                    unreachable!()
                };
                let params = self.parameters(&function)?;
                found.extend(
                    violations
                        .into_iter()
                        .map(|v| tagged("argument", &params[index].0, v)),
                );
                self.parameters_next(site, function, args, index + 1, found)?;
            }
            Request::CallEnd {
                environment,
                program,
            } => {
                self.environment = environment;
                self.program = program;
                self.depth -= 1;
            }
            Request::BodyEnd { result_type, body } => {
                self.environment.pop();
                if let Some(ty) = result_type {
                    let value = self.value();
                    self.push(Request::ResultAnnotation(body, value));
                    self.eval(ty, true);
                }
            }
            Request::CallTrace(site, function) => {
                if let Some(Err(error)) = &mut self.answer {
                    let graph = &self.program.graph;
                    let mut location = Hash::new();
                    if let Some(file) = graph.source(site) {
                        location.insert(s("file"), s(file));
                    }
                    location.insert(s("path"), s(&graph.path(site)));
                    let span = graph.span(site);
                    location.insert(s("line"), Value::Integer(span.line as i64));
                    location.insert(s("column"), Value::Integer(span.col as i64));
                    location.insert(
                        s("function"),
                        s(&function.program.graph.path(function.node)),
                    );
                    if let Some(file) = function.program.graph.source(function.node) {
                        location.insert(s("function_file"), s(file));
                    }
                    let definition = function.program.graph.span(function.node);
                    location.insert(s("function_line"), Value::Integer(definition.line as i64));
                    location.insert(s("function_column"), Value::Integer(definition.col as i64));
                    error.calls.push(Value::Hash(location));
                }
            }
            Request::ResultAnnotation(body, value) => {
                let ty = self.value();
                self.push(Request::ResultChecked(body, value.clone()));
                self.push(Request::Atomic(Atomic {
                    site: body,
                    nodes: Vec::new(),
                    operation: Operation::Violations(value, ty),
                    answers: Vec::new(),
                }));
            }
            Request::ResultChecked(body, value) => {
                let Value::Array(found) = self.value() else {
                    unreachable!()
                };
                if !found.is_empty() {
                    return Err(self.violation_error(body, "the result", found.to_vec()));
                }
                self.finish(value);
            }
            Request::Branch { name, args, index } => self.branch(name, args, index)?,
            Request::Try(handler) => match self.take() {
                Err(error) => self.push(Request::CallArgument(handler, vec![error.to_value()])),
                Ok(value) => self.finish(value),
            },
            Request::Finally(cleanup) => {
                let outcome = self.take();
                self.push(Request::Cleanup(outcome));
                self.eval(cleanup, true);
            }
            Request::Cleanup(outcome) => {
                if let Err(error) = self.take() {
                    self.fail(error);
                } else {
                    self.answer = Some(outcome);
                }
            }
            Request::FormValue {
                site,
                name,
                args,
                mut values,
                index,
            } => {
                values.push(self.value());
                self.form_values(site, name, args, values, index + 1)?;
            }
            Request::Iterate { .. } => self.iterate(request)?,
            Request::Iterated { iteration, item } => self.iterated(*iteration, item)?,
            Request::UntilTest(iteration) => self.until_test(*iteration)?,
            Request::UntilNext(iteration) => {
                let value = self.value();
                let Request::Iterate {
                    site,
                    name,
                    args,
                    values,
                    index,
                    output,
                    count,
                    ..
                } = *iteration
                else {
                    unreachable!()
                };
                self.push(Request::Iterate {
                    site,
                    name,
                    args,
                    values,
                    index,
                    output,
                    state: value,
                    count: count + 1,
                });
            }
            Request::Refine(site, args, base) => {
                let check = self.value();
                let Value::Function(function) = &check else {
                    return Err(self.err(
                        args[1],
                        "type",
                        "expected a function (`[fn, [value], body]` or its name)",
                    ));
                };
                if self.parameters(function)?.len() != 1 {
                    return Err(self.err(
                        args[1],
                        "arity",
                        "a refinement check takes exactly 1 parameter",
                    ));
                }
                self.finish(
                    builtins::refine(base, check).map_err(|f| self.from_fault(site, &args, f))?,
                );
            }
            Request::Refined(site, args, _) => {
                let base = self.value();
                self.push(Request::Refine(site, args.clone(), base));
                self.eval(args[1], true);
            }
            Request::Atomic(operation) => self.atomic(operation)?,
            Request::PredicateEnd(mut operation) => {
                let verdict = match self.take() {
                    Ok(Value::Boolean(v)) => Ok(v),
                    Ok(other) => Err(format!(
                        "returned {}, expected boolean",
                        builtins::type_of(&other)
                    )),
                    Err(error) => Err(format!("{}: {}", error.kind, error.message)),
                };
                operation.answers.push(verdict);
                self.push(Request::Atomic(operation));
            }
            Request::AdvanceChild => {
                unreachable!("child advancement is dispatched by the VM scheduler")
            }
        }
        Ok(())
    }
    fn evaluate(&mut self, id: NodeId, mode: Mode) -> Result<(), HelixError> {
        self.remaining_work = self.remaining_work.checked_sub(1).ok_or_else(|| {
            self.err(
                id,
                "limit",
                format!("evaluation exceeds {} node visits", self.limits.max_work),
            )
        })?;
        if self.active.len() >= self.limits.max_eval_depth {
            return Err(self.err(
                id,
                "depth",
                format!(
                    "evaluation nested deeper than {} nodes",
                    self.limits.max_eval_depth
                ),
            ));
        }
        if mode == Mode::Argument {
            if let Some(name) = self.graph().symbol(id) {
                match self.resolve(id, name) {
                    Some(Binding::Value(value)) => {
                        self.charge_value(id, &value)?;
                        self.finish(value);
                        return Ok(());
                    }
                    Some(Binding::Node(target)) => {
                        self.eval(target, false);
                        return Ok(());
                    }
                    None if builtins::is_primitive(name) => {
                        self.finish(s(name));
                        return Ok(());
                    }
                    None => {
                        return Err(self.err(id, "unbound", format!("unbound symbol `{name}`")));
                    }
                }
            }
        }
        if let Some(value) = self.memo.get(&id).cloned() {
            self.charge_value(id, &value)?;
            self.finish(value);
            return Ok(());
        }
        let local = self.in_function(id);
        let depth = if local { self.depth } else { 0 };
        if !self.active.insert((id, depth)) {
            return Err(self.err(id, "cycle", "value depends on itself"));
        }
        self.push(Request::Finish(id, depth, local));
        match self.graph().kind(id).clone() {
            Kind::Scalar { value, .. } => self.finish(Value::from_yaml(&value)),
            Kind::Map(entries) => {
                let (keys, nodes) = entries.into_iter().unzip();
                self.collect(id, None, nodes, Some(keys), false);
            }
            Kind::Seq(items) => {
                let head = items.first().copied();
                if let Some(name) = head
                    .and_then(|h| self.graph().symbol(h))
                    .map(str::to_string)
                {
                    if builtins::arity(&name).is_some() {
                        self.apply(id, Actor::Builtin(name), items[1..].to_vec())?;
                    } else if let Some(binding) = self.resolve(head.unwrap(), &name) {
                        self.push(Request::Actor(id, items));
                        match binding {
                            Binding::Value(v) => self.finish(v),
                            Binding::Node(n) => self.eval(n, false),
                        }
                    } else {
                        self.collect(id, None, items, None, false);
                    }
                } else if head.is_some_and(|h| matches!(self.graph().kind(h), Kind::Seq(_))) {
                    self.push(Request::Actor(id, items.clone()));
                    self.eval(head.unwrap(), true);
                } else {
                    self.collect(id, None, items, None, false);
                }
            }
        }
        Ok(())
    }
    fn collect(
        &mut self,
        site: NodeId,
        actor: Option<Actor>,
        nodes: Vec<NodeId>,
        keys: Option<Vec<String>>,
        argument: bool,
    ) {
        self.collect_next(site, actor, nodes, keys, Vec::new(), argument);
    }
    fn collect_next(
        &mut self,
        site: NodeId,
        actor: Option<Actor>,
        nodes: Vec<NodeId>,
        keys: Option<Vec<String>>,
        values: Vec<Value>,
        argument: bool,
    ) {
        let index = values.len();
        if let Some(&node) = nodes.get(index) {
            self.push(Request::Collect {
                site,
                actor,
                nodes,
                keys,
                values,
                argument,
            });
            self.eval(node, argument);
        } else if let Some(actor) = actor {
            self.push(Request::Apply(site, actor, nodes, values));
        } else if let Some(keys) = keys {
            self.finish(Value::Hash(
                keys.into_iter().map(|k| s(&k)).zip(values).collect(),
            ));
        } else {
            self.finish(Value::Array(values.into()));
        }
    }
    fn apply(&mut self, id: NodeId, actor: Actor, args: Vec<NodeId>) -> Result<(), HelixError> {
        if let Actor::Builtin(name) = &actor {
            let (min, max) = builtins::arity(name).unwrap();
            if args.len() < min || max.is_some_and(|max| args.len() > max) {
                let expected = match max {
                    Some(m) if m == min => arguments(m),
                    Some(m) => format!("{min} to {}", arguments(m)),
                    None => format!("at least {}", arguments(min)),
                };
                return Err(self.err(
                    id,
                    "arity",
                    format!("`{name}` takes {expected}, got {}", args.len()),
                ));
            }
            if builtins::is_form(name) {
                return self.form(id, name, args);
            }
        }
        self.collect(id, Some(actor), args, None, true);
        Ok(())
    }
    fn form(&mut self, id: NodeId, name: &str, args: Vec<NodeId>) -> Result<(), HelixError> {
        match name {
            "type_time" => {
                let previous = self.type_time;
                self.type_time = true;
                let memo = if previous {
                    None
                } else {
                    Some(std::mem::take(&mut self.memo))
                };
                self.push(Request::TypeEnd { previous, memo });
                if let Some(import) = self.program.modules.file_import() {
                    return Err(self.err(import,"effect","file import is not allowed in type time; use embedded std modules or supply source data directly"));
                }
                self.eval(args[0], true);
            }
            "fn" => {
                let function = Arc::new(Function {
                    program: self.program.clone(),
                    node: id,
                    environment: self.capture_environment(id),
                });
                self.parameters(&function)?;
                self.finish(Value::Function(function));
            }
            "closure" => {
                if !matches!(self.graph().kind(args[0]), Kind::Map(_))
                    || self.graph().form(args[1]) != Some("fn")
                {
                    return Err(self.err(id, "type", "a closure needs bindings and a function"));
                }
                self.eval(args[1], false);
            }
            "let" => {
                if !matches!(self.graph().kind(args[0]), Kind::Map(_)) {
                    return Err(self.err(args[0], "type", "`let` bindings must be a map"));
                }
                self.eval(args[1], true);
            }
            "import" => {
                let root=self.program.modules.root_of(id).ok_or_else(||self.err(id,"import","an import must be a literal `[import, SPEC]` in the program or a module it imports"))?;
                self.eval(root, false);
            }
            "if" | "cond" | "and" | "or" => {
                if name == "cond" && args.len().is_multiple_of(2) {
                    return Err(self.err(
                        id,
                        "arity",
                        "`cond` takes condition/value pairs followed by a default",
                    ));
                }
                self.push(Request::Branch {
                    name: name.into(),
                    args: args.clone(),
                    index: 0,
                });
                self.eval(args[0], true);
            }
            "try" => {
                self.push(Request::Try(args[1]));
                self.eval(args[0], true);
            }
            "finally" => {
                self.push(Request::Finally(args[1]));
                self.eval(args[0], true);
            }
            "refine" => {
                self.push(Request::Refined(id, args.clone(), Value::Null));
                self.eval(args[0], true);
            }
            "vm" => {
                self.form_values(id, name.into(), args, Vec::new(), 0)?;
            }
            "call" | "map" | "filter" | "each" | "fold" | "until" => {
                self.form_values(id, name.into(), args, Vec::new(), 0)?
            }
            _ => unreachable!("unknown form {name}"),
        }
        Ok(())
    }
    fn branch(&mut self, name: String, args: Vec<NodeId>, index: usize) -> Result<(), HelixError> {
        let value = self.value();
        let condition = self.truth(&value, args[index])?;
        match name.as_str() {
            "if" => self.eval(args[if condition { 1 } else { 2 }], true),
            "cond" => {
                if condition {
                    self.eval(args[index + 1], true);
                } else if index + 2 == args.len() - 1 {
                    self.eval(args[index + 2], true);
                } else {
                    let node = args[index + 2];
                    self.push(Request::Branch {
                        name,
                        args,
                        index: index + 2,
                    });
                    self.eval(node, true);
                }
            }
            _ => {
                let stop = name == "or";
                if condition == stop {
                    self.finish(Value::Boolean(stop));
                } else if index + 1 == args.len() {
                    self.finish(Value::Boolean(!stop));
                } else {
                    let node = args[index + 1];
                    self.push(Request::Branch {
                        name,
                        args,
                        index: index + 1,
                    });
                    self.eval(node, true);
                }
            }
        }
        Ok(())
    }
    fn form_values(
        &mut self,
        site: NodeId,
        name: String,
        args: Vec<NodeId>,
        values: Vec<Value>,
        index: usize,
    ) -> Result<(), HelixError> {
        // Evaluate callbacks after collection operands, preserving the original form order.
        let positions: Vec<usize> = match name.as_str() {
            "call" => (1..args.len()).collect(),
            "vm" => vec![0],
            "fold" => vec![1, 2],
            "until" => vec![2],
            _ => vec![1],
        };
        if let Some(&position) = positions.get(index) {
            let node = args[position];
            self.push(Request::FormValue {
                site,
                name,
                args,
                values,
                index,
            });
            self.eval(node, true);
            return Ok(());
        }
        match name.as_str() {
            "call" => self.push(Request::CallArgument(args[0], values)),
            "vm" => {
                let Value::Function(function) = &values[0] else {
                    return Err(self.err(
                        args[0],
                        "type",
                        "`vm` requires a zero-argument function",
                    ));
                };
                if !self.parameters(function)?.is_empty() {
                    return Err(self.err(
                        args[0],
                        "arity",
                        "`vm` requires a zero-argument function",
                    ));
                }
                self.finish(Value::Machine(Arc::new(Vm::from_function(
                    function.clone(),
                ))));
            }
            _ => {
                let (items, state) = match name.as_str() {
                    "fold" => (
                        builtins::list(values[1].clone(), 0)
                            .map_err(|f| self.err(args[2], &f.kind, f.message))?,
                        values[0].clone(),
                    ),
                    "until" => (Vec::new(), values[0].clone()),
                    _ => (
                        builtins::list(values[0].clone(), 0)
                            .map_err(|f| self.err(args[1], &f.kind, f.message))?,
                        Value::Null,
                    ),
                };
                self.push(Request::Iterate {
                    site,
                    name,
                    args,
                    values: items,
                    index: 0,
                    output: Vec::new(),
                    state,
                    count: 0,
                });
            }
        }
        Ok(())
    }
    fn iterate(&mut self, iteration: Request) -> Result<(), HelixError> {
        let Request::Iterate {
            site,
            ref name,
            ref args,
            ref values,
            index,
            ref output,
            ref state,
            count,
        } = iteration
        else {
            unreachable!()
        };
        if name == "until" {
            if count >= self.limits.max_iterations {
                return Err(self.err(
                    site,
                    "limit",
                    format!(
                        "`until` did not finish within {} steps",
                        self.limits.max_iterations
                    ),
                ));
            }
            let callback = args[0];
            let arg = state.clone();
            self.push(Request::UntilTest(Box::new(iteration)));
            self.push(Request::CallArgument(callback, vec![arg]));
        } else if let Some(item) = values.get(index).cloned() {
            let callback = args[0];
            let arguments = if name == "fold" {
                vec![state.clone(), item.clone()]
            } else {
                vec![item.clone()]
            };
            self.push(Request::Iterated {
                iteration: Box::new(iteration),
                item,
            });
            self.push(Request::CallArgument(callback, arguments));
        } else {
            self.finish(match name.as_str() {
                "fold" => state.clone(),
                "each" => Value::Null,
                _ => Value::Array(output.clone().into()),
            });
        }
        Ok(())
    }
    fn iterated(&mut self, iteration: Request, item: Value) -> Result<(), HelixError> {
        let value = self.value();
        let Request::Iterate {
            site,
            name,
            args,
            values,
            index,
            mut output,
            mut state,
            count,
        } = iteration
        else {
            unreachable!()
        };
        match name.as_str() {
            "map" => output.push(value),
            "filter" => {
                if self.truth(&value, args[0])? {
                    output.push(item)
                }
            }
            "fold" => state = value,
            _ => {}
        }
        self.push(Request::Iterate {
            site,
            name,
            args,
            values,
            index: index + 1,
            output,
            state,
            count,
        });
        Ok(())
    }
    fn until_test(&mut self, iteration: Request) -> Result<(), HelixError> {
        let verdict = self.value();
        let Request::Iterate {
            ref args,
            ref state,
            ..
        } = iteration
        else {
            unreachable!()
        };
        if self.truth(&verdict, args[0])? {
            self.finish(state.clone());
        } else {
            let callback = args[1];
            let arg = state.clone();
            self.push(Request::UntilNext(Box::new(iteration)));
            self.push(Request::CallArgument(callback, vec![arg]));
        }
        Ok(())
    }
    fn parameters(&self, function: &Function) -> Result<Vec<(String, Option<NodeId>)>, HelixError> {
        let graph = &function.program.graph;
        let id = function.node;
        let Kind::Seq(items) = graph.kind(id) else {
            unreachable!()
        };
        if !matches!(items.len(), 3 | 4) {
            return Err(graph.error(
                id,
                "arity",
                format!(
                    "`fn` takes 2 to 3 arguments, got {}",
                    items.len().saturating_sub(1)
                ),
            ));
        }
        let malformed = || {
            graph.error(
                items[1],
                "type",
                "parameters must be a list of names or `[name, type]` pairs".into(),
            )
        };
        let Kind::Seq(params) = graph.kind(items[1]) else {
            return Err(malformed());
        };
        params
            .iter()
            .map(|&p| match graph.kind(p) {
                Kind::Seq(pair) if pair.len() == 2 => Ok((
                    graph.symbol(pair[0]).ok_or_else(malformed)?.into(),
                    Some(pair[1]),
                )),
                _ => Ok((graph.symbol(p).ok_or_else(malformed)?.into(), None)),
            })
            .collect()
    }
    fn parameters_next(
        &mut self,
        site: NodeId,
        function: Arc<Function>,
        args: Vec<Value>,
        mut index: usize,
        found: Vec<Value>,
    ) -> Result<(), HelixError> {
        let params = self.parameters(&function)?;
        while index < params.len() {
            if let Some(annotation) = params[index].1 {
                self.push(Request::ParameterType {
                    site,
                    function,
                    args,
                    index,
                    found,
                    annotation,
                });
                self.eval(annotation, true);
                return Ok(());
            }
            index += 1;
        }
        if !found.is_empty() {
            return Err(self.violation_error(
                site,
                &format!(
                    "the arguments of `{}`",
                    function.program.graph.path(function.node)
                ),
                found,
            ));
        }
        let Kind::Seq(items) = function.program.graph.kind(function.node) else {
            unreachable!()
        };
        let body = items[2];
        let result_type = items.get(3).copied();
        self.environment.push(Scope {
            function: function.node,
            args,
        });
        self.push(Request::BodyEnd { result_type, body });
        self.eval(body, true);
        Ok(())
    }
    fn capture_environment(&self, node: NodeId) -> Vec<Scope> {
        let mut ancestors = HashSet::new();
        let mut parent = self.graph().parent(node);
        while let Some(id) = parent {
            if self.graph().form(id) == Some("fn") {
                ancestors.insert(id);
            }
            parent = self.graph().parent(id);
        }
        self.environment
            .iter()
            .filter(|scope| ancestors.contains(&scope.function))
            .cloned()
            .collect()
    }

    fn resolve(&self, from: NodeId, name: &str) -> Option<Binding> {
        if let Some(found) = self.resolve_name(from, name) {
            return Some(found);
        }
        let (head, rest) = name.split_once('.')?;
        let mut binding = self.resolve_name(from, head)?;
        for part in rest.split('.') {
            binding = match binding {
                Binding::Node(node) => {
                    let node = self.program.modules.root_of(node).unwrap_or(node);
                    let Kind::Map(entries) = self.graph().kind(node) else {
                        return None;
                    };
                    Binding::Node(entries.iter().find(|(k, _)| k == part)?.1)
                }
                Binding::Value(Value::Hash(map)) => Binding::Value(map.get(&s(part))?.clone()),
                _ => return None,
            };
        }
        Some(binding)
    }
    fn resolve_name(&self, from: NodeId, name: &str) -> Option<Binding> {
        let entry = |entries: &[(String, NodeId)]| {
            entries
                .iter()
                .find(|(k, _)| k == name)
                .map(|(_, id)| Binding::Node(*id))
        };
        let mut child = from;
        while let Some(id) = self.graph().parent(child) {
            let found = match self.graph().kind(id) {
                Kind::Map(entries) => entry(entries),
                Kind::Seq(items) if items.get(2) == Some(&child) => {
                    match (self.graph().form(id), self.graph().kind(items[1])) {
                        (Some("fn"), _) => {
                            let function = Function {
                                program: self.program.clone(),
                                node: id,
                                environment: Vec::new(),
                            };
                            self.parameters(&function)
                                .ok()
                                .and_then(|p| p.iter().position(|(n, _)| n == name))
                                .and_then(|i| {
                                    self.environment
                                        .iter()
                                        .rev()
                                        .find(|f| f.function == id)
                                        .map(|f| Binding::Value(f.args[i].clone()))
                                })
                        }
                        (Some("let" | "closure"), Kind::Map(entries)) => entry(entries),
                        _ => None,
                    }
                }
                _ => None,
            };
            if found.is_some() {
                return found;
            }
            child = id;
        }
        None
    }
    fn in_function(&self, id: NodeId) -> bool {
        let mut node = self.graph().parent(id);
        while let Some(parent) = node {
            if self.graph().form(parent) == Some("fn") {
                return true;
            }
            node = self.graph().parent(parent)
        }
        false
    }
    fn truth(&self, value: &Value, blame: NodeId) -> Result<bool, HelixError> {
        builtins::boolean(value, 0).map_err(|f| self.err(blame, &f.kind, f.message))
    }
    fn from_fault(&self, site: NodeId, args: &[NodeId], fault: Fault) -> HelixError {
        HelixError {
            cause: fault.cause,
            data: fault.data,
            ..self.err(
                fault.arg.and_then(|i| args.get(i).copied()).unwrap_or(site),
                &fault.kind,
                fault.message,
            )
        }
    }
    fn violation_error(&self, blame: NodeId, what: &str, found: Vec<Value>) -> HelixError {
        let first = found
            .iter()
            .find_map(|v| {
                if let Value::Hash(h) = v {
                    h.get(&s("message")).and_then(Value::as_str)
                } else {
                    None
                }
            })
            .unwrap_or_default();
        let plural = if found.len() == 1 { "" } else { "s" };
        let mut error = self.err(
            blame,
            "type",
            format!("{} type violation{plural} in {what}: {first}", found.len()),
        );
        error.data = Some(Value::Hash(
            [(s("violations"), Value::Array(found.into()))]
                .into_iter()
                .collect(),
        ));
        error
    }
    fn atomic(&mut self, operation: Atomic) -> Result<(), HelixError> {
        let mut cursor = 0;
        let mut pending = None;
        let mut pred = |check: &Value, value: &Value| {
            let index = cursor;
            cursor += 1;
            if let Some(answer) = operation.answers.get(index) {
                return answer.clone();
            }
            if pending.is_none() {
                pending = Some((check.clone(), value.clone()));
            }
            Err("pending predicate".into())
        };
        let result = match &operation.operation {
            Operation::Builtin(name, args) => builtins::apply(name, args, &mut pred)
                .map_err(|f| self.from_fault(operation.site, &operation.nodes, f)),
            Operation::Violations(value, ty) => builtins::violations(value, ty, &mut pred)
                .map(|found| {
                    Value::Array(found.iter().map(builtins::Violation::to_value).collect())
                })
                .map_err(|why| self.err(operation.site, "type", why)),
        };
        if let Some((check, value)) = pending {
            let site = operation.site;
            self.push(Request::PredicateEnd(operation));
            match check {
                Value::Function(function) => {
                    if self.parameters(&function)?.len() != 1 {
                        self.fail(self.err(
                            site,
                            "type",
                            "a refinement check takes exactly 1 parameter",
                        ));
                    } else {
                        self.push(Request::Call(site, function, vec![value]));
                    }
                }
                _ => self.fail(self.err(
                    site,
                    "type",
                    "a refinement check must be `[fn, [value], body]`",
                )),
            }
        } else {
            self.answer = Some(result);
        }
        Ok(())
    }
    fn charge_value(&mut self, id: NodeId, value: &Value) -> Result<(), HelixError> {
        let mut pending = vec![(value, 0, true)];
        let mut nodes = 0;
        let mut produced = 0;
        while let Some((value, depth, graph_node)) = pending.pop() {
            if depth > crate::graph::MAX_NESTING {
                return Err(self.err(
                    id,
                    "depth",
                    format!(
                        "value nested deeper than {} containers",
                        crate::graph::MAX_NESTING
                    ),
                ));
            }
            nodes += usize::from(graph_node);
            if nodes > crate::graph::MAX_NODES {
                return Err(self.err(
                    id,
                    "limit",
                    format!("value exceeds {} nodes", crate::graph::MAX_NODES),
                ));
            }
            let size = match value {
                Value::String(text) => 1 + text.len(),
                _ => 1,
            };
            produced += size;
            if produced > self.limits.max_value_size {
                return Err(self.err(
                    id,
                    "limit",
                    format!("value exceeds {} units", self.limits.max_value_size),
                ));
            }
            self.remaining_values = self.remaining_values.checked_sub(size).ok_or_else(|| {
                self.err(
                    id,
                    "limit",
                    format!(
                        "evaluation exceeds {} produced value units",
                        self.limits.max_values
                    ),
                )
            })?;
            match value {
                Value::Array(items) => pending.extend(items.iter().map(|v| (v, depth + 1, true))),
                Value::Hash(entries) => pending.extend(
                    entries
                        .iter()
                        .flat_map(|(k, v)| [(k, depth + 1, false), (v, depth + 1, true)]),
                ),
                _ => {}
            }
        }
        Ok(())
    }
    pub fn inspection(&self) -> Value {
        let graph = self.graph();
        let requests = self
            .requests
            .iter()
            .rev()
            .map(|r| r.view(graph))
            .collect::<Vec<_>>();
        let location = requests
            .iter()
            .find_map(|request| {
                if let Value::Hash(fields) = request {
                    fields
                        .get(&s("location"))
                        .or_else(|| fields.get(&s("site")))
                        .cloned()
                } else {
                    None
                }
            })
            .unwrap_or(Value::Null);
        let mut active = self.active.iter().copied().collect::<Vec<_>>();
        active.sort_unstable();
        Value::Hash(
            [
                (s("continuations"), Value::Array(requests.into())),
                (s("location"), location),
                (
                    s("phase"),
                    if self.type_time {
                        s("type")
                    } else {
                        Value::Null
                    },
                ),
                (
                    s("budgets"),
                    Value::Hash(
                        [
                            (
                                s("remaining_work"),
                                Value::Integer(self.remaining_work as i64),
                            ),
                            (
                                s("remaining_values"),
                                Value::Integer(self.remaining_values as i64),
                            ),
                        ]
                        .into_iter()
                        .collect(),
                    ),
                ),
                (
                    s("active"),
                    Value::Array(
                        active
                            .into_iter()
                            .map(|(node, depth)| {
                                Value::Hash(
                                    [
                                        (s("location"), graph.reference(node)),
                                        (s("depth"), Value::Integer(depth as i64)),
                                    ]
                                    .into_iter()
                                    .collect(),
                                )
                            })
                            .collect(),
                    ),
                ),
                (
                    s("environments"),
                    Value::Array(
                        self.environment
                            .iter()
                            .map(|scope| {
                                Value::Hash(
                                    [
                                        (s("function"), graph.reference(scope.function)),
                                        (s("arguments"), Value::Array((scope.args.clone()).into())),
                                    ]
                                    .into_iter()
                                    .collect(),
                                )
                            })
                            .collect(),
                    ),
                ),
                (
                    s("intermediate"),
                    self.answer
                        .as_ref()
                        .map(outcome_view)
                        .unwrap_or(Value::Null),
                ),
                (
                    s("limits"),
                    Value::Hash(
                        [
                            (s("call_depth"), Value::Integer(self.depth as i64)),
                            (s("max_depth"), Value::Integer(self.limits.max_depth as i64)),
                            (s("max_work"), Value::Integer(self.limits.max_work as i64)),
                            (
                                s("max_values"),
                                Value::Integer(self.limits.max_values as i64),
                            ),
                            (
                                s("max_value_size"),
                                Value::Integer(self.limits.max_value_size as i64),
                            ),
                            (
                                s("max_eval_depth"),
                                Value::Integer(self.limits.max_eval_depth as i64),
                            ),
                            (
                                s("max_iterations"),
                                Value::Integer(self.limits.max_iterations as i64),
                            ),
                        ]
                        .into_iter()
                        .collect(),
                    ),
                ),
                (
                    s("memoized"),
                    Value::Array({
                        let mut entries = self.memo.iter().collect::<Vec<_>>();
                        entries.sort_by_key(|(node, _)| **node);
                        entries
                            .into_iter()
                            .map(|(node, value)| {
                                Value::Hash(
                                    [
                                        (s("location"), graph.reference(*node)),
                                        (s("value"), value.clone()),
                                    ]
                                    .into_iter()
                                    .collect(),
                                )
                            })
                            .collect()
                    }),
                ),
            ]
            .into_iter()
            .collect(),
        )
    }
    pub fn recovery_source(&self) -> String {
        crate::emit::recovery(self.graph(), &self.memo)
    }

    pub fn check_nodes(&self) -> Vec<NodeId> {
        let mut pending = vec![ROOT];
        let mut nodes = Vec::new();
        while let Some(id) = pending.pop() {
            match self.graph().kind(id) {
                Kind::Map(entries) => pending.extend(entries.iter().rev().map(|(_, child)| *child)),
                Kind::Seq(items)
                    if self.graph().form(id).is_none_or(|name| {
                        builtins::arity(name).is_none() && self.resolve(id, name).is_none()
                    }) =>
                {
                    pending.extend(items.iter().rev())
                }
                _ => nodes.push(id),
            }
        }
        nodes
    }
    pub fn for_node(&self, node: NodeId) -> Self {
        let mut state = Self::start(
            self.program.clone(),
            Vec::new(),
            Request::Eval(node, Mode::Node),
        );
        state.memo = self.memo.clone();
        state.limits = self.limits;
        state.remaining_work = self.remaining_work;
        state.remaining_values = self.remaining_values;
        state
    }
}
fn arguments(n: usize) -> String {
    if n == 1 {
        "1 argument".into()
    } else {
        format!("{n} arguments")
    }
}
fn tagged(label: &str, name: &str, violation: Value) -> Value {
    let mut h = Hash::new();
    h.insert(s(label), s(name));
    if let Value::Hash(fields) = violation {
        h.extend(fields)
    }
    Value::Hash(h)
}

fn object(kind: &str, fields: Vec<(&str, Value)>) -> Value {
    Value::Hash(
        std::iter::once((s("kind"), s(kind)))
            .chain(fields.into_iter().map(|(k, v)| (s(k), v)))
            .collect(),
    )
}
fn outcome_view(outcome: &Outcome) -> Value {
    match outcome {
        Ok(value) => object("return", vec![("value", value.clone())]),
        Err(error) => object("error", vec![("error", error.to_value())]),
    }
}
fn scope_view(graph: &Graph, scope: &Scope) -> Value {
    object(
        "scope",
        vec![
            ("function", graph.reference(scope.function)),
            ("arguments", Value::Array((scope.args.clone()).into())),
        ],
    )
}
impl Atomic {
    fn view(&self, graph: &Graph) -> Value {
        let operation = match &self.operation {
            Operation::Builtin(name, values) => object(
                name,
                vec![("arguments", Value::Array(values.clone().into()))],
            ),
            Operation::Violations(value, ty) => object(
                "violations",
                vec![("value", value.clone()), ("type", ty.clone())],
            ),
        };
        object(
            "atomic",
            vec![
                ("site", graph.reference(self.site)),
                (
                    "arguments",
                    Value::Array(self.nodes.iter().map(|n| graph.reference(*n)).collect()),
                ),
                ("operation", operation),
                (
                    "predicate_answers",
                    Value::Array(
                        self.answers
                            .iter()
                            .map(|a| match a {
                                Ok(v) => Value::Boolean(*v),
                                Err(e) => s(e),
                            })
                            .collect(),
                    ),
                ),
            ],
        )
    }
}
impl Request {
    fn view(&self, g: &Graph) -> Value {
        let nodes =
            |items: &[NodeId]| Value::Array(items.iter().map(|n| g.reference(*n)).collect());
        let values = |items: &[Value]| Value::Array((items.to_vec()).into());
        let function = |f: &Arc<Function>| Value::Function(f.clone());
        let actor = |a: &Actor| match a {
            Actor::Builtin(name) => s(name),
            Actor::Function(f) => function(f),
        };
        let number = |n: usize| Value::Integer(n as i64);
        match self {
            Self::TypeEnd { previous, memo } => object(
                "type_time_return",
                vec![
                    ("previous", Value::Boolean(*previous)),
                    (
                        "memo",
                        memo.as_ref()
                            .map(|memo| {
                                let mut entries = memo.iter().collect::<Vec<_>>();
                                entries.sort_by_key(|(id, _)| **id);
                                Value::Array(
                                    entries
                                        .into_iter()
                                        .map(|(node, value)| {
                                            object(
                                                "memo",
                                                vec![
                                                    ("location", g.reference(*node)),
                                                    ("value", value.clone()),
                                                ],
                                            )
                                        })
                                        .collect(),
                                )
                            })
                            .unwrap_or(Value::Null),
                    ),
                ],
            ),
            Self::Eval(id, mode) => object(
                "evaluate",
                vec![
                    ("location", g.reference(*id)),
                    ("symbolic", Value::Boolean(*mode == Mode::Argument)),
                ],
            ),
            Self::Finish(id, depth, local) => object(
                "finish_node",
                vec![
                    ("location", g.reference(*id)),
                    ("depth", number(*depth)),
                    ("local", Value::Boolean(*local)),
                ],
            ),
            Self::Actor(id, args) => object(
                "resolve_actor",
                vec![("site", g.reference(*id)), ("nodes", nodes(args))],
            ),
            Self::Collect {
                site,
                actor: a,
                nodes: ns,
                keys,
                values: vs,
                argument,
            } => object(
                "collect",
                vec![
                    ("site", g.reference(*site)),
                    ("actor", a.as_ref().map(actor).unwrap_or(Value::Null)),
                    ("nodes", nodes(ns)),
                    (
                        "keys",
                        keys.as_ref()
                            .map(|ks| Value::Array(ks.iter().map(|k| s(k)).collect()))
                            .unwrap_or(Value::Null),
                    ),
                    ("values", values(vs)),
                    ("symbolic", Value::Boolean(*argument)),
                ],
            ),
            Self::Apply(site, a, ns, vs) => object(
                "apply",
                vec![
                    ("site", g.reference(*site)),
                    ("actor", actor(a)),
                    ("nodes", nodes(ns)),
                    ("values", values(vs)),
                ],
            ),
            Self::Call(site, f, vs) => object(
                "call",
                vec![
                    ("site", g.reference(*site)),
                    ("function", function(f)),
                    ("arguments", values(vs)),
                ],
            ),
            Self::CallTrace(site, f) => object(
                "call_trace",
                vec![("site", g.reference(*site)), ("function", function(f))],
            ),
            Self::CallArgument(site, vs) | Self::CallResolved(site, vs) => object(
                if matches!(self, Self::CallArgument(..)) {
                    "call_argument"
                } else {
                    "call_resolved"
                },
                vec![("site", g.reference(*site)), ("arguments", values(vs))],
            ),
            Self::CallEnd {
                environment,
                program,
            } => object(
                "return_call",
                vec![(
                    "environment",
                    Value::Array(
                        environment
                            .iter()
                            .map(|e| scope_view(&program.graph, e))
                            .collect(),
                    ),
                )],
            ),
            Self::BodyEnd { result_type, body } => object(
                "return_body",
                vec![
                    (
                        "result_type",
                        result_type.map(|n| g.reference(n)).unwrap_or(Value::Null),
                    ),
                    ("body", g.reference(*body)),
                ],
            ),
            Self::ResultAnnotation(id, v) | Self::ResultChecked(id, v) => object(
                if matches!(self, Self::ResultAnnotation(..)) {
                    "result_annotation"
                } else {
                    "result_checked"
                },
                vec![("body", g.reference(*id)), ("value", v.clone())],
            ),
            Self::Parameters {
                site,
                function: f,
                args,
                index,
                found,
            }
            | Self::ParameterChecked {
                site,
                function: f,
                args,
                index,
                found,
            } => object(
                if matches!(self, Self::Parameters { .. }) {
                    "parameters"
                } else {
                    "parameter_checked"
                },
                vec![
                    ("site", g.reference(*site)),
                    ("function", function(f)),
                    ("arguments", values(args)),
                    ("index", number(*index)),
                    ("violations", values(found)),
                ],
            ),
            Self::ParameterType {
                site,
                function: f,
                args,
                index,
                found,
                annotation,
            } => object(
                "parameter_type",
                vec![
                    ("site", g.reference(*site)),
                    ("function", function(f)),
                    ("arguments", values(args)),
                    ("index", number(*index)),
                    ("violations", values(found)),
                    ("annotation", g.reference(*annotation)),
                ],
            ),
            Self::Branch { name, args, index } => object(
                name,
                vec![("nodes", nodes(args)), ("index", number(*index))],
            ),
            Self::Try(id) | Self::Finally(id) => object(
                if matches!(self, Self::Try(..)) {
                    "try"
                } else {
                    "finally"
                },
                vec![("node", g.reference(*id))],
            ),
            Self::Cleanup(outcome) => object("cleanup", vec![("outcome", outcome_view(outcome))]),
            Self::Iterate {
                site,
                name,
                args,
                values: vs,
                index,
                output,
                state,
                count,
            } => object(
                "iterate",
                vec![
                    ("site", g.reference(*site)),
                    ("operation", s(name)),
                    ("nodes", nodes(args)),
                    ("values", values(vs)),
                    ("index", number(*index)),
                    ("output", values(output)),
                    ("state", state.clone()),
                    ("count", number(*count)),
                ],
            ),
            Self::Iterated { iteration, item } => object(
                "iterated",
                vec![("iteration", iteration.view(g)), ("item", item.clone())],
            ),
            Self::UntilTest(iteration) | Self::UntilNext(iteration) => object(
                if matches!(self, Self::UntilTest(..)) {
                    "until_test"
                } else {
                    "until_next"
                },
                vec![("iteration", iteration.view(g))],
            ),
            Self::FormValue {
                site,
                name,
                args,
                values: vs,
                index,
            } => object(
                "form_value",
                vec![
                    ("site", g.reference(*site)),
                    ("operation", s(name)),
                    ("nodes", nodes(args)),
                    ("values", values(vs)),
                    ("index", number(*index)),
                ],
            ),
            Self::Refine(site, args, v) | Self::Refined(site, args, v) => object(
                if matches!(self, Self::Refine(..)) {
                    "refine"
                } else {
                    "refined"
                },
                vec![
                    ("site", g.reference(*site)),
                    ("nodes", nodes(args)),
                    ("base", v.clone()),
                ],
            ),
            Self::Atomic(operation) | Self::PredicateEnd(operation) => object(
                if matches!(self, Self::Atomic(..)) {
                    "atomic"
                } else {
                    "predicate_return"
                },
                vec![("operation", operation.view(g))],
            ),
            Self::AdvanceChild => object("advance_child", Vec::new()),
        }
    }
}
