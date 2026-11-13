//! Pure host calls and function annotations share predicate-aware checking.
//! All callback work returns to the same explicit request engine.
use crate::builtins::{self, Ask, Fault};
use crate::graph::Effects;
use crate::model::{
    Atomic, Evaluator, Function, Hash, HelixError, NodeId, Operation, Request, Scope, Value,
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
        self.schedule_operation(site, Vec::new(), Operation::Violations(value, ty));
    }
    fn schedule_operation(&mut self, site: NodeId, nodes: Vec<NodeId>, operation: Operation) {
        self.push(Request::Atomic(Atomic {
            site,
            nodes,
            operation,
            answers: Vec::new(),
        }));
    }
    pub(super) fn parameter_checked(
        &mut self,
        site: NodeId,
        function: Arc<Function>,
        args: Vec<Value>,
        index: usize,
        mut found: Vec<Value>,
    ) -> Result<(), HelixError> {
        let Value::Array(violations) = self.value() else {
            unreachable!()
        };
        let params = self.parameters(&function)?;
        found.extend(
            violations
                .into_iter()
                .map(|v| argument_violation(&params[index].0, v)),
        );
        self.parameters_next(site, function, args, index + 1, found)?;
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
                "expected a function (`{$call: [fn, [value], body]}` or its name)",
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
    pub(super) fn predicate_end(&mut self, mut operation: Atomic) {
        let answer = self
            .take()
            .map_err(|error| format!("{}: {}", error.kind, error.message));
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
                self.eval(annotation);
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
        error
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
        let result = match &operation.operation {
            Operation::Builtin(name, args) => builtins::prepare_policy(name, args, &mut pred)
                .and_then(|prepared| {
                    if let Some(call) = prepared {
                        policy = Some((builtins::policy_module(name).expect("registered policy"), call));
                        Ok(Value::Null)
                    } else {
                        builtins::apply(name, args, &mut pred)
                    }
                })
                .map_err(|f| self.from_fault(operation.site, &operation.nodes, f)),
            Operation::Violations(value, ty) => builtins::violations(value, ty, &mut pred)
                .map(|found| {
                    Value::Array(found.iter().map(builtins::Violation::to_value).collect())
                })
                .map_err(|why| self.err(operation.site, "type", why)),
        };
        if let Some(asked) = pending {
            let site = operation.site;
            self.push(Request::PredicateEnd(operation));
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
                        self.push(Request::Call(site, function, vec![value]));
                    }
                }
                _ => self.fail(self.err(
                    site,
                    "type",
                    "a refinement check must be `{$call: [fn, [value], body]}`",
                )),
            }
        } else if let Some((module, call)) = policy {
            let root = self.program.modules.policies[module];
            let crate::graph::Syntax::Mapping(fields) = self.graph().syntax(root) else { unreachable!() };
            let node = fields.iter().find(|(name, _)| name == call.function).expect("registered policy function").1;
            let function = Arc::new(Function { program: self.program.clone(), node, environment: Vec::new() });
            self.push(Request::Call(operation.site, function, call.args));
        } else {
            self.answer = Some(result);
        }
        Ok(())
    }
}

fn argument_violation(name: &str, violation: Value) -> Value {
    let mut h = Hash::new();
    h.insert(s("argument"), s(name));
    if let Value::Hash(fields) = violation {
        h.extend(fields)
    }
    Value::Hash(h)
}
