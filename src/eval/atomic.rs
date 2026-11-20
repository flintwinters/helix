//! Pure host calls and function annotations share predicate-aware checking.
//! All callback work returns to the same explicit request engine.
use crate::builtins::{self, Ask, Fault};
use crate::graph::Effects;
use crate::model::{
    Atomic, CallOrigin, Evaluator, Function, Hash, HelixError, NodeId, Operation, Request, Scope, Segment, Value,
    string as s,
};
use std::sync::Arc;

/// What a pure host operation is waiting for the engine to answer.
enum Pending {
    Predicate(Value, Value),
    Signature(Value, bool),
}

impl Evaluator {
    pub(super) fn schedule_builtin(
        &mut self,
        site: NodeId,
        nodes: Vec<NodeId>,
        name: String,
        values: Vec<Value>,
    ) {
        self.schedule_operation(site, nodes, Operation::Builtin(name, values));
    }
    pub(super) fn schedule_check(&mut self, site: NodeId, value: Value, ty: Value) {
        let site = self.declaration_node(site);
        self.schedule_operation(site, Vec::new(), Operation::Violations(value, ty));
    }
    fn declaration_node(&self, mut site: NodeId) -> NodeId {
        let mut seen = std::collections::HashSet::new();
        while seen.insert(site) {
            let crate::graph::Syntax::Reference(name) = self.graph().syntax(site) else { break };
            let Some(target) = self.named_node(site, name) else { break };
            site = target;
        }
        site
    }
    fn schedule_operation(&mut self, site: NodeId, nodes: Vec<NodeId>, operation: Operation) {
        self.push(Request::Atomic(Atomic {
            site,
            nodes,
            operation,
            answers: Vec::new(),
            cause: None,
            predicate_failed: false,
        }));
    }
    pub(super) fn parameter_checked(
        &mut self,
        site: NodeId,
        origin: CallOrigin,
        function: Arc<Function>,
        args: Vec<Value>,
        index: usize,
        ty: Value,
        mut found: Vec<Value>,
    ) -> Result<(), HelixError> {
        let Value::Array(violations) = self.value() else {
            unreachable!()
        };
        let params = self.parameters(&function)?;
        if violations.is_empty() {
            let node = origin.args.get(index).copied().unwrap_or(origin.site);
            self.note_annotation("parameter", origin.program.graph.reference(node), params[index].1.expect("checked annotation"), ty, args[index].clone());
        }
        found.extend(
            violations
                .into_iter()
                .map(|v| argument_violation(&params[index].0, &origin, index, v)),
        );
        self.parameters_next(site, origin, function, args, index + 1, found)?;
        Ok(())
    }
    pub(super) fn refine(
        &mut self,
        site: NodeId,
        args: &[NodeId],
        base: Value,
    ) -> Result<(), HelixError> {
        let check = self.value();
        let Value::Function(function) = &check else {
            return Err(self.err(
                args[1],
                "type",
                "expected a function (`!call [fn, [value], body]` or its name)",
            ));
        };
        if self.parameters(function)?.len() != 1 {
            return Err(self.err(
                args[1],
                "arity",
                "a refinement check takes exactly 1 parameter",
            ));
        }
        self.finish(builtins::refine(base, check).map_err(|f| self.from_fault(site, args, f))?);
        Ok(())
    }
    pub(super) fn predicate_end(&mut self, mut operation: Atomic, predicate: bool) {
        let outcome = self.take();
        if predicate && !operation.predicate_failed && !matches!(&outcome, Ok(Value::Boolean(_))) {
            operation.predicate_failed = true;
            operation.cause = outcome.as_ref().err().map(HelixError::to_value);
        }
        let answer = outcome.map_err(|error| {
            format!("{}: {}", error.kind, error.message)
        });
        operation.answers.push(answer);
        self.push(Request::Atomic(operation));
    }
    /// Evaluate a function's declared parameter and result types as the function would at a call
    /// (its own lexical environment) and answer the signature type; undeclared ones are `any`.
    pub(super) fn signature_of(&mut self, site: NodeId, function: Arc<Function>, pure: bool) -> Result<(), HelixError> {
        if self.depth >= self.limits.max_depth {
            return Err(self.err(site, "depth", format!("calls nested deeper than {}", self.limits.max_depth)));
        }
        let environment = std::mem::replace(&mut self.environment, function.environment.clone());
        let program = std::mem::replace(&mut self.program, function.program.clone());
        self.depth += 1;
        self.push(Request::CallEnd { environment, program });
        self.signature_next(function, Vec::new(), pure)
    }
    pub(super) fn signature_next(&mut self, function: Arc<Function>, mut found: Vec<Value>, pure: bool) -> Result<(), HelixError> {
        let params = self.parameters(&function)?;
        let result = function.program.graph.call(function.node).and_then(|items| items.get(3).copied());
        while found.len() <= params.len() {
            let declared = match params.get(found.len()) {
                Some((_, annotation)) => *annotation,
                None => result,
            };
            let Some(node) = declared else {
                found.push(s("any"));
                continue;
            };
            self.push(Request::SignatureSlot(function, found, pure));
            self.eval(node);
            return Ok(());
        }
        let result = found.pop().expect("a result slot");
        let pure = pure && self.graph().effects(function.node, &|node| self.program.modules.root_of(node)) == Effects::None;
        self.finish(builtins::function_type(found, result, pure));
        Ok(())
    }
    pub(super) fn parameters_next(
        &mut self,
        site: NodeId,
        origin: CallOrigin,
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
                    origin,
                    function,
                    args,
                    index,
                    found,
                    annotation,
                });
                self.eval(annotation);
                return Ok(());
            }
            index += 1;
        }
        if !found.is_empty() {
            let mut error = self.violation_error(
                function.node,
                &format!(
                    "the arguments of `{}`",
                    function.program.graph.path(function.node)
                ),
                found.clone(),
            );
            let first_argument = found.first().and_then(|v| if let Value::Hash(fields) = v {
                fields.get(&s("argument")).and_then(Value::as_str)
            } else { None });
            let node = first_argument.and_then(|name| params.iter().position(|(parameter, _)| parameter == name))
                .and_then(|index| origin.args.get(index)).copied().unwrap_or(origin.site);
            let location = origin.program.graph.error(node, "type", String::new());
            error.file = location.file;
            error.path = location.path;
            error.reference = location.reference;
            error.span = location.span;
            return Err(self.blame_predicate(error));
        }
        let Some(items) = function.program.graph.call(function.node) else {
            unreachable!()
        };
        let body = items[2];
        let result_type = items.get(3).copied();
        self.environment.push(Scope {
            function: function.node,
            args,
            born: self.scopes,
        });
        self.scopes += 1;
        self.push(Request::BodyEnd { result_type, body });
        self.eval(body);
        Ok(())
    }
    fn from_fault(&self, site: NodeId, args: &[NodeId], fault: Fault) -> HelixError {
        self.blame_predicate(HelixError {
            cause: fault.cause,
            data: fault.data,
            ..self.err(
                fault.arg.and_then(|i| args.get(i).copied()).unwrap_or(site),
                &fault.kind,
                fault.message,
            )
        })
    }
    /// Predicate execution failures are distinct from an ordinary rejected value.
    /// Their location is emitted by the shared checker from the retained function.
    fn blame_predicate(&self, mut error: HelixError) -> HelixError {
        let Some(Value::Hash(data)) = &error.data else { return error };
        let Some(Value::Array(violations)) = data.get(&s("violations")) else { return error };
        let broken = violations.iter().find_map(|v| {
            let Value::Hash(fields) = v else { return None };
            (fields.get(&s("rule")).and_then(Value::as_str) == Some("predicate_error"))
                .then(|| fields.get(&s("predicate"))).flatten()
        });
        let Some(Value::Hash(location)) = broken else { return error };
        error.kind = "predicate".into();
        error.file = location.get(&s("source")).and_then(Value::as_str)
            .filter(|source| *source != "<root>").map(str::to_owned);
        error.path = location.get(&s("path")).and_then(Value::as_str).map(str::to_owned);
        if let Some(Value::Integer(line)) = location.get(&s("line")) { error.span.line = *line as usize; }
        if let Some(Value::Integer(column)) = location.get(&s("column")) { error.span.col = *column as usize; }
        if let Some(Value::Array(parts)) = location.get(&s("reference")) {
            error.reference = Some(parts.iter().filter_map(|part| match part {
                Value::String(key) => Some(Segment::Key(key.clone())),
                Value::Integer(index) => Some(Segment::Index(*index as usize)),
                _ => None,
            }).collect());
        }
        error
    }
    pub(super) fn violation_error(
        &self,
        blame: NodeId,
        what: &str,
        found: Vec<Value>,
    ) -> HelixError {
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
        self.blame_predicate(error)
    }
    pub(super) fn atomic(&mut self, operation: Atomic) -> Result<(), HelixError> {
        let mut cursor = 0;
        let mut pending = None;
        let mut pred = |ask: Ask| {
            let index = cursor;
            cursor += 1;
            if let Some(answer) = operation.answers.get(index) {
                return answer.clone();
            }
            if pending.is_none() {
                pending = Some(match ask {
                    Ask::Predicate(check, value) => Pending::Predicate(check.clone(), value.clone()),
                    Ask::Signature(function, pure) => Pending::Signature(function.clone(), pure),
                });
            }
            Err("pending answer".into())
        };
        let mut policy = None;
        let mut result = match &operation.operation {
            Operation::Builtin(name, args) => builtins::prepare_policy(name, args, &mut pred)
                .and_then(|prepared| {
                    if let Some(call) = prepared {
                        policy = Some((builtins::policy_module(name).expect("registered policy"), call));
                        Ok(Value::Null)
                    } else {
                        builtins::apply(name, args, &mut pred)
                    }
                })
                .map_err(|f| {
                    let mut nodes = operation.nodes.clone();
                    if matches!(name.as_str(), "is" | "violations" | "expect") && f.arg == Some(1) {
                        if let Some(node) = nodes.get_mut(1) { *node = self.declaration_node(*node); }
                    }
                    self.from_fault(operation.site, &nodes, f)
                }),
            Operation::Violations(value, ty) => builtins::violations(value, ty, &mut pred)
                .map_err(|why| self.err(operation.site, "type", why))
                .and_then(|found| {
                    let broken = found.iter().any(|v| v.rule() == "predicate_error" || v.is_declaration());
                    let values = found.iter().map(builtins::Violation::to_value).collect::<Vec<_>>();
                    if broken { Err(self.violation_error(operation.site, "the predicate", values)) }
                    else { Ok(Value::Array(values.into())) }
                }),
        };
        if let Some(asked) = pending {
            let site = operation.site;
            self.push(Request::PredicateEnd(operation, matches!(&asked, Pending::Predicate(..))));
            match asked {
                Pending::Signature(Value::Function(function), pure) => {
                    self.push(Request::SignatureOf(site, function, pure));
                }
                Pending::Predicate(Value::Function(function), value) => {
                    if self.parameters(&function)?.len() != 1 {
                        self.fail(self.err(
                            site,
                            "type",
                            "a refinement check takes exactly 1 parameter",
                        ));
                    } else {
                        self.push(Request::Call(site, function, vec![value], Vec::new()));
                    }
                }
                _ => self.fail(self.err(
                    site,
                    "type",
                    "a refinement check must be `!call [fn, [value], body]`",
                )),
            }
        } else if let Some((module, call)) = policy {
            let root = self.program.modules.policies[module];
            let crate::graph::Syntax::Mapping(fields) = self.graph().syntax(root) else { unreachable!() };
            let node = fields.iter().find(|(name, _)| name == call.function).expect("registered policy function").1;
            let function = Arc::new(Function { program: self.program.clone(), node, environment: Vec::new() });
            self.push(Request::Call(operation.site, function, call.args, Vec::new()));
        } else {
            if let Err(error) = &mut result {
                if error.kind == "predicate" && error.cause.is_none() { error.cause = operation.cause; }
            } else if let Operation::Builtin(name, args) = &operation.operation {
                if name == "expect" {
                    self.note_annotation("expect", self.graph().reference(operation.nodes[0]),
                        operation.nodes[1], args[1].clone(), args[0].clone());
                }
            }
            self.answer = Some(result);
        }
        Ok(())
    }
}

fn argument_violation(name: &str, origin: &CallOrigin, index: usize, violation: Value) -> Value {
    let mut h = Hash::new();
    h.insert(s("argument"), s(name));
    let graph = &origin.program.graph;
    let node = origin.args.get(index).copied().unwrap_or(origin.site);
    let span = graph.span(node);
    h.insert(s("argument_location"), Value::Hash([
        (s("reference"), graph.reference(node)),
        (s("path"), s(&graph.path(node))),
        (s("line"), Value::Integer(span.line as i64)),
        (s("column"), Value::Integer(span.col as i64)),
    ].into_iter().collect()));
    if let Value::Hash(fields) = violation {
        h.extend(fields)
    }
    Value::Hash(h)
}
