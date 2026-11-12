//! Special forms choose argument order and schedule work on the shared request engine.
use crate::builtins::{self, Descriptor, Evaluation, Form};
use crate::model::{Evaluator, Function, HelixError, Iteration, Kind, NodeId, Request, Value, Vm};
use std::sync::Arc;

impl Evaluator {
    pub(super) fn truth(&self, value: &Value, blame: NodeId) -> Result<bool, HelixError> {
        builtins::boolean(value, 0).map_err(|f| self.err(blame, &f.kind, f.message))
    }

    pub(super) fn form(
        &mut self,
        id: NodeId,
        actor: &Descriptor,
        args: Vec<NodeId>,
    ) -> Result<(), HelixError> {
        let name = actor.name;
        let Evaluation::Form(form) = actor.evaluation else {
            unreachable!("form initiation requires a registered form")
        };
        match form {
            Form::TypeTime => {
                let previous = self.type_time;
                self.type_time = true;
                let memo = if previous {
                    None
                } else {
                    Some(std::mem::take(&mut self.memo))
                };
                self.push(Request::TypeEnd { previous, memo });
                if let Some(import) = self.program.modules.file_import() {
                    return Err(self.err(import,"effect","type_time requires a program with no file imports, including unused imports; use embedded std modules or supply source data directly"));
                }
                self.eval(args[0]);
            }
            Form::Fn => {
                let function = Arc::new(Function {
                    program: self.program.clone(),
                    node: id,
                    environment: self.capture_environment(id),
                });
                self.parameters(&function)?;
                self.finish(Value::Function(function));
            }
            Form::Closure => {
                if !matches!(self.graph().kind(args[0]), Kind::Map(_))
                    || self.graph().form(args[1]) != Some("fn")
                {
                    return Err(self.err(id, "type", "a closure needs bindings and a function"));
                }
                self.eval(args[1]);
            }
            Form::Let => {
                if !matches!(self.graph().kind(args[0]), Kind::Map(_)) {
                    return Err(self.err(args[0], "type", "`let` bindings must be a map"));
                }
                self.eval(args[1]);
            }
            Form::Import => {
                let root=self.program.modules.root_of(id).ok_or_else(||self.err(id,"import","an import must be a literal `{$call: [import, SPEC]}` in the program or a module it imports"))?;
                self.eval(root);
            }
            Form::If | Form::Cond | Form::And | Form::Or => {
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
                self.eval(args[0]);
            }
            Form::Try => {
                self.push(Request::Try(args[1]));
                self.eval(args[0]);
            }
            Form::Finally => {
                self.push(Request::Finally(args[1]));
                self.eval(args[0]);
            }
            Form::Refine => {
                self.push(Request::Refined(id, args.clone(), Value::Null));
                self.eval(args[0]);
            }
            Form::Vm => {
                self.form_values(id, name.into(), args, Vec::new(), 0)?;
            }
            Form::Call | Form::Map | Form::Filter | Form::Each | Form::Fold | Form::Until => {
                self.form_values(id, name.into(), args, Vec::new(), 0)?
            }
        }
        Ok(())
    }
    pub(super) fn branch(
        &mut self,
        name: String,
        args: Vec<NodeId>,
        index: usize,
    ) -> Result<(), HelixError> {
        let value = self.value();
        let condition = self.truth(&value, args[index])?;
        match name.as_str() {
            "if" => self.eval(args[if condition { 1 } else { 2 }]),
            "cond" => {
                if condition {
                    self.eval(args[index + 1]);
                } else if index + 2 == args.len() - 1 {
                    self.eval(args[index + 2]);
                } else {
                    let node = args[index + 2];
                    self.push(Request::Branch {
                        name,
                        args,
                        index: index + 2,
                    });
                    self.eval(node);
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
                    self.eval(node);
                }
            }
        }
        Ok(())
    }
    pub(super) fn form_values(
        &mut self,
        site: NodeId,
        name: String,
        args: Vec<NodeId>,
        values: Vec<Value>,
        index: usize,
    ) -> Result<(), HelixError> {
        // Evaluate callbacks after collection operands, preserving the original form order.
        let position = match name.as_str() {
            "call" => (index + 1 < args.len()).then_some(index + 1),
            "vm" => (index == 0).then_some(0),
            "fold" => [1, 2].get(index).copied(),
            "until" => (index == 0).then_some(2),
            _ => (index == 0).then_some(1),
        };
        if let Some(position) = position {
            let node = args[position];
            self.push(Request::FormValue {
                site,
                name,
                args,
                values,
                index,
            });
            self.eval(node);
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
                self.push(Request::Iterate(Iteration {
                    site,
                    name,
                    args,
                    values: items,
                    index: 0,
                    output: Vec::new(),
                    state,
                    count: 0,
                }));
            }
        }
        Ok(())
    }
}
