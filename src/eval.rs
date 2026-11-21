//! Explicit execution requests and continuations. No transition invokes another interpreter.
use crate::graph::{Graph, NodeId, ROOT};
use crate::link;
use crate::model::{CallOrigin, HelixError, Segment, string as s};
use crate::value::{Hash, Value};
use std::path::{Path, PathBuf};
use std::sync::Arc;

mod atomic;
mod annotations;
mod effects;
mod expression;
mod forms;
mod iteration;
mod lexical;
mod preflight;

use crate::model::Limits;
use crate::model::{Outcome, Vm};

use crate::model::{Actor, Program, Request, Scope};
pub(crate) use crate::model::{Evaluator, Function};
impl Evaluator {
    pub fn new(
        mut graph: Graph,
        main: Option<&Path>,
        roots: Option<&[PathBuf]>,
    ) -> Result<Self, HelixError> {
        let modules = link::link(&mut graph, main, roots)?;
        let mut evaluator = Self::start(
            Arc::new(Program {
                code: graph.code_views(),
                graph,
                modules,
            }),
            Vec::new(),
            Request::Eval(ROOT),
        );
        let regions = evaluator.preflight_regions();
        evaluator.requests.extend(regions.into_iter().rev().map(Request::Eval));
        Ok(evaluator)
    }
    fn start(program: Arc<Program>, environment: Vec<Scope>, request: Request) -> Self {
        let limits = Limits::default();
        Self {
            program,
            environment,
            memo: Default::default(),
            active: Default::default(),
            requests: vec![request],
            answer: None,
            depth: 0,
            type_time: false,
            inherited_type_time: false,
            pending_effect: None,
            effects: Vec::new().into(),
            type_inputs: Vec::new().into(),
            annotations: Vec::new().into(),
            evaluated: Vec::new().into(),
            evaluated_index: Default::default(),
            evaluated_units: 0,
            evaluated_truncated: false,
            annotation_units: 0,
            annotations_truncated: false,
            region: None,
            scopes: 0,
            effects_allowed: true,
            signal: None,
            remaining_work: limits.max_work,
            remaining_values: limits.max_values,
            limits,
        }
    }
    pub(crate) fn invoke(function: Arc<Function>, args: Vec<Value>) -> Self {
        Self::start(function.program.clone(), function.environment.clone(),
            Request::Call(function.node, function, args, Vec::new()))
    }
    pub fn child(function: Arc<Function>) -> Self {
        Self::start(
            function.program.clone(),
            function.environment.clone(),
            Request::Call(function.node, function, Vec::new(), Vec::new()),
        )
    }
    fn graph(&self) -> &Graph {
        &self.program.graph
    }
    fn err(&self, id: NodeId, kind: &str, message: impl Into<String>) -> HelixError {
        self.phased(self.graph().error(id, kind, message.into()))
    }
    /// Mark an error raised while this evaluator runs in type time.
    fn phased(&self, mut error: HelixError) -> HelixError {
        if self.type_time || self.inherited_type_time {
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
    fn eval(&mut self, node: NodeId) {
        self.push(Request::Eval(node));
    }
    fn finish(&mut self, value: Value) {
        self.answer = Some(Ok(value));
    }
    fn fail(&mut self, mut error: HelixError) {
        if self.type_time || self.inherited_type_time {
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
                    self.finish_region()?;
                }
            }
            Request::Eval(id) => self.evaluate(id)?,
            Request::Finish(id, depth, local) => {
                self.active.remove(&(id, depth));
                if let Some(Ok(value)) = &self.answer {
                    let value = value.clone();
                    self.charge_value(id, &value)?;
                    self.note_evaluated(id, &value);
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
                    let written = self.graph().identifier(items[0]);
                    let subject = written.map_or_else(|| "a computed call actor".to_string(), |name| format!("call actor `{name}`"));
                    return Err(self.err(
                        items[0],
                        "type",
                        format!("{subject} must resolve to a function, got {}", crate::builtins::type_name(&value)),
                    ).with_hint(crate::graph::not_callable(written, &value)));
                }
            }
            Request::Project { site, name, members, actor } => {
                let mut value = self.value();
                for (index, member) in members.iter().enumerate() {
                    let found = match &value {
                        Value::Hash(map) => map.get(&s(member)).cloned(),
                        _ => None,
                    };
                    value = found.ok_or_else(|| {
                        // Members are the trailing parts of `name`; the written prefix precedes them.
                        let written = &name[..name.len() - members[index..].iter().map(|m| m.len() + 1).sum::<usize>()];
                        self.err(site, "unbound", format!("unbound {} `{name}`", if actor { "actor" } else { "symbol" }))
                            .with_hint(Some(crate::graph::missing_member(written, crate::graph::members_of(&value), member)))
                    })?;
                }
                self.charge_value(site, &value)?;
                self.finish(value);
            }
            Request::Collect {
                site,
                actor,
                nodes,
                keys,
                mut values,
            } => {
                values.push(self.value());
                self.collect_next(site, actor, nodes, keys, values);
            }
            Request::Apply(site, actor, nodes, values) => match actor {
                Actor::Builtin(name) if name == "effect" => {
                    self.request_effect(site, &nodes, &values)?
                }

                Actor::Builtin(name) if name == "http_serve" => {
                    let Value::Function(handler) = &values[1] else {
                        return Err(self.err(site, "type", "HTTP handler must be a function"));
                    };
                    crate::graph::http::validate_function(handler,1).map_err(|why| self.err(site,"type",why))?;
                    crate::graph::http::validate_options(&values[0])
                        .map_err(|why| self.err(site, "type", why))?;
                    let Value::Hash(options) = &values[0] else { unreachable!() };
                    let bind = options.get(&s("bind")).unwrap().clone();
                    self.request_effect(site, &[site, site], &[s("http_serve"), bind])?;
                    self.push(Request::Serve(site, values[0].clone(), handler.clone()));
                }
                Actor::Function(function) => self.push(Request::Call(site, function, values, nodes)),
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
                Actor::Builtin(name) => self.schedule_builtin(site, nodes, name, values),
            },
            Request::Serve(_, _, _) => {
                let outcome = self.take();
                self.answer = Some(outcome);
            }
            Request::Call(site, function, args, nodes) => {
                let origin = CallOrigin { program: self.program.clone(), site, args: nodes };
                self.push(Request::CallTrace(site, function.clone()));
                let params = self.parameters(&function)?;
                if params.len() != args.len() {
                    return Err(self.err(
                        site,
                        "arity",
                        format!(
                            "the function defined at {} takes {}{}, got {}",
                            function.program.graph.place(function.node),
                            arguments(params.len()),
                            if params.is_empty() { String::new() } else {
                                format!(" ({})", params.iter().map(|(name, _)| name.as_str()).collect::<Vec<_>>().join(", "))
                            },
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
                    origin,
                    function,
                    args,
                    index: 0,
                    found: Vec::new(),
                });
            }
            Request::CallArgument(designator, args, nodes) => {
                self.push(Request::CallResolved(designator, args, nodes));
                self.eval(designator);
            }
            Request::CallResolved(site, args, nodes) => {
                let Value::Function(function) = self.value() else {
                    return Err(self.err(
                        site,
                        "type",
                        "expected a function (`!call [fn, [params], body]` or its name)",
                    ));
                };
                self.push(Request::Call(site, function, args, nodes));
            }
            Request::Parameters {
                site,
                origin,
                function,
                args,
                index,
                found,
            } => self.parameters_next(site, origin, function, args, index, found)?,
            Request::ParameterType {
                site,
                origin,
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
                    origin,
                    function,
                    args,
                    index,
                    ty: ty.clone(),
                    found,
                });
                self.schedule_check(annotation, value, ty);
            }
            Request::ParameterChecked {
                site,
                origin,
                function,
                args,
                index,
                ty,
                found,
            } => self.parameter_checked(site, origin, function, args, index, ty, found)?,
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
                    self.push(Request::ResultAnnotation(body, ty, value));
                    self.eval(ty);
                }
            }
            Request::CallTrace(site, function) => {
                if let Some(Err(error)) = &mut self.answer {
                    let graph = &self.program.graph;
                    let mut location = Hash::new();
                    if let Some(file) = graph.source(site) {
                        location.insert(s("file"), s(file));
                    }
                    location.insert(s("reference"), Segment::render(&graph.segments(site)));
                    let span = graph.span(site);
                    location.insert(s("line"), Value::Integer(span.line as i64));
                    location.insert(s("column"), Value::Integer(span.col as i64));
                    if let Some(file) = function.program.graph.source(function.node) {
                        location.insert(s("function_file"), s(file));
                    }
                    let definition = function.program.graph.span(function.node);
                    location.insert(s("function_line"), Value::Integer(definition.line as i64));
                    location.insert(s("function_column"), Value::Integer(definition.col as i64));
                    error.calls.push(Value::Hash(location));
                }
            }
            Request::ResultAnnotation(body, declaration, value) => {
                let ty = self.value();
                self.push(Request::ResultChecked(body, declaration, value.clone(), ty.clone()));
                self.schedule_check(declaration, value, ty);
            }
            Request::ResultChecked(body, declaration, value, ty) => {
                let Value::Array(found) = self.value() else {
                    unreachable!()
                };
                if !found.is_empty() {
                    return Err(self.violation_error(body, "the result", found.to_vec()));
                }
                self.note_annotation("result", self.graph().reference(body), declaration, ty, value.clone());
                self.finish(value);
            }
            Request::Branch { name, args, index } => self.branch(name, args, index)?,
            Request::Try(handler) => match self.take() {
                Err(error) => self.push(Request::CallArgument(handler, vec![error.to_value()], Vec::new())),
                Ok(value) => self.finish(value),
            },
            Request::Finally(cleanup) => {
                let outcome = self.take();
                self.push(Request::Cleanup(outcome));
                self.eval(cleanup);
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
            Request::Iterate(iteration) => self.iterate(iteration)?,
            Request::Iterated { iteration, item } => self.iterated(iteration, item)?,
            Request::UntilTest(iteration) => self.until_test(iteration)?,
            Request::UntilNext(mut iteration) => {
                iteration.state = self.value();
                iteration.count += 1;
                self.push(Request::Iterate(iteration));
            }
            Request::Refine(site, args, base) => self.refine(site, &args, base)?,
            Request::Refined(site, args, _) => {
                let base = self.value();
                self.push(Request::Refine(site, args.clone(), base));
                self.eval(args[1]);
            }
            Request::Atomic(operation) => self.atomic(operation)?,
            Request::PredicateEnd(operation, predicate) => self.predicate_end(operation, predicate),
            Request::SignatureOf(site, function, pure) => self.signature_of(site, function, pure)?,
            Request::SignatureSlot(function, mut found, pure) => {
                found.push(self.value());
                self.signature_next(function, found, pure)?;
            }
            Request::AdvanceChild => {
                unreachable!("child advancement is dispatched by the VM scheduler")
            }
        }
        Ok(())
    }
    fn charge_value(&mut self, id: NodeId, value: &Value) -> Result<(), HelixError> {
        let crate::model::Measure { units, nodes, height, .. } = value.measure();
        if height > crate::graph::MAX_NESTING {
            return Err(self.err(
                id,
                "depth",
                format!("value nested deeper than {} containers", crate::graph::MAX_NESTING),
            ));
        }
        if nodes > crate::graph::MAX_NODES {
            return Err(self.err(
                id,
                "limit",
                format!("value exceeds {} nodes", crate::graph::MAX_NODES),
            ));
        }
        if units > self.limits.max_value_size {
            return Err(self.err(
                id,
                "limit",
                format!("value exceeds {} units", self.limits.max_value_size),
            ));
        }
        self.remaining_values = self.remaining_values.checked_sub(units).ok_or_else(|| {
            self.err(
                id,
                "limit",
                format!("evaluation exceeds {} produced value units", self.limits.max_values),
            )
        })?;
        Ok(())
    }

    pub fn for_node(&self, node: NodeId) -> Self {
        let mut state = Self::start(self.program.clone(), Vec::new(), Request::Eval(node));
        state.effects_allowed = false;
        state.memo = self.memo.clone();
        // Memoized closures retain frame birth stamps; subsequent regions must see those
        // frames as older inputs, and keep the input ledger accumulated by prior checks.
        state.scopes = self.scopes;
        state.type_inputs = self.type_inputs.clone();
        state.annotations = self.annotations.clone();
        state.annotation_units = self.annotation_units;
        state.annotations_truncated = self.annotations_truncated;
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
