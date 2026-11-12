//! Finite, named object graphs. Inspection never runs the inspected machine.
use crate::model::{Binding, Function, Hash, Program, Value, Vm, string as s};
use std::sync::Arc;

mod evaluation;
mod error;

pub(crate) fn state(value: &Value) -> Value {
    let mut view = View::default();
    let root = view.value(value);
    view.complete(root)
}
pub(crate) fn machine(machine: &Vm) -> Value {
    let mut view = View::default();
    let root = view.machine_body(machine);
    view.complete(root)
}
#[derive(Clone)]
enum Pending {
    Runtime(Value),
    Program(Arc<Program>, bool),
}
#[derive(Default)]
struct View {
    objects: Hash,
    pending: Vec<Pending>,
}
impl View {
    fn reference(index: usize) -> Value {
        Value::Hash(
            [(s("ref"), s(&format!("object{index}")))]
                .into_iter()
                .collect(),
        )
    }
    fn program(&mut self, program: Arc<Program>, policies: bool) -> Value {
        let index = self
            .pending
            .iter()
            .position(
                |p| matches!(p,Pending::Program(p, _) if Arc::ptr_eq(p,&program) || **p==*program),
            )
            .unwrap_or_else(|| {
                self.pending.push(Pending::Program(program, policies));
                self.pending.len() - 1
            });
        if let Pending::Program(_, expose) = &mut self.pending[index] { *expose |= policies; }
        Self::reference(index)
    }
    fn function_body(&mut self, function: &Function) -> Value {
        let graph = &function.program.graph;
        let environment = Value::Array(
            function
                .environment
                .iter()
                .map(|scope| {
                    Value::Hash(
                        [
                            (s("function"), graph.reference(scope.function)),
                            (s("arguments"), Value::Array(scope.args.clone().into())),
                        ]
                        .into_iter()
                        .collect(),
                    )
                })
                .collect(),
        );
        let bindings = Value::Hash(
            function
                .free_bindings(function.node)
                .into_iter()
                .map(|(name, binding)| {
                    (
                        s(&name),
                        match binding {
                            Binding::Node(node) => Value::Hash(
                                [(s("code"), graph.reference(node))].into_iter().collect(),
                            ),
                            Binding::Value(value) => value,
                        },
                    )
                })
                .collect(),
        );
        Value::Hash(
            [
                (s("kind"), s("function")),
                (s("program"), self.program(function.program.clone(), function.program.modules.roots.difference(&function.program.modules.visible_roots).any(|root| graph.source(*root) == graph.source(function.node)))),
                (s("code"), function.program.code[function.node].clone()),
                (s("location"), graph.reference(function.node)),
                (s("environment"), self.value(&environment)),
                (s("bindings"), self.value(&bindings)),
            ]
            .into_iter()
            .collect(),
        )
    }
    fn machine_body(&mut self, machine: &Vm) -> Value {
        let mut body = self.value(&machine.inspection());
        if let (Some(evaluator), Value::Hash(fields)) = (&machine.evaluator, &mut body) {
            let hidden_sources = evaluator.program.modules.roots.difference(&evaluator.program.modules.visible_roots)
                .filter_map(|root| evaluator.program.graph.source(*root)).collect::<Vec<_>>();
            let policy_error = machine.error.iter().chain(&machine.errors).any(|error| {
                error.file.as_deref().is_some_and(|source| hidden_sources.contains(&source))
                    || error.calls.iter().any(|call| {
                        let Value::Hash(fields) = call else { return false; };
                        ["file", "function_file"].iter().any(|key| fields.get(&s(key)).and_then(Value::as_str)
                            .is_some_and(|source| hidden_sources.contains(&source)))
                    })
            });
            fields.insert(s("program"), self.program(evaluator.program.clone(), !evaluator.requests.is_empty() || policy_error));
        }
        body
    }
    fn complete(mut self, mut root: Value) -> Value {
        let mut index = 0;
        while index < self.pending.len() {
            let body = match self.pending[index].clone() {
                Pending::Runtime(Value::Function(function)) => self.function_body(&function),
                Pending::Runtime(Value::Machine(machine)) => self.machine_body(&machine),
                Pending::Program(..) => { index += 1; continue; }
                _ => unreachable!("only functions, machines and programs are named objects"),
            };
            self.objects.insert(s(&format!("object{index}")), body);
            index += 1;
        }
        // Runtime traversal can discover a policy closure after its program was
        // named. Render programs last so source exposure includes every reference.
        for (index, pending) in self.pending.iter().enumerate() {
            let body = match pending.clone() {
                Pending::Program(program, policies) => {
                    let graph = &program.graph;
                    let mut roots = vec![crate::model::ROOT];
                    roots.extend(if policies { &program.modules.roots } else { &program.modules.visible_roots }.iter().copied());
                    roots.sort_unstable();
                    roots.dedup();
                    Value::Hash(
                        [
                            (s("kind"), s("program")),
                            (
                                s("sources"),
                                Value::Array(
                                    roots
                                        .into_iter()
                                        .map(|root| {
                                            Value::Hash(
                                                [
                                                    (
                                                        s("source"),
                                                        s(graph.source(root).unwrap_or("<root>")),
                                                    ),
                                                    (s("code"), program.code[root].clone()),
                                                ]
                                                .into_iter()
                                                .collect(),
                                            )
                                        })
                                        .collect(),
                                ),
                            ),
                        ]
                        .into_iter()
                        .collect(),
                    )
                }
                _ => continue,
            };
            self.objects.insert(s(&format!("object{index}")), body);
        }
        if self.objects.len() == 0 {
            return root;
        }
        let Value::Hash(fields) = &mut root else {
            unreachable!("state and machine inspection have fixed map envelopes")
        };
        fields.insert(s("objects"), Value::Hash(self.objects));
        root
    }

    fn value(&mut self, value: &Value) -> Value {
        match value {
            Value::Array(items) => Value::Array(items.iter().map(|v| self.value(v)).collect()),
            Value::Hash(entries) => Value::Hash(
                entries
                    .iter()
                    .map(|(k, v)| (k.clone(), self.value(v)))
                    .collect(),
            ),
            Value::Function(_) | Value::Machine(_) => {
                let index = self
                    .pending
                    .iter()
                    .position(|v| matches!(v,Pending::Runtime(v) if v.same_view(value)))
                    .unwrap_or_else(|| {
                        self.pending.push(Pending::Runtime(value.clone()));
                        self.pending.len() - 1
                    });
                Self::reference(index)
            }
            other => other.clone(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exhausted_policy_keeps_its_error_source_without_a_live_continuation() {
        let mut vm = Vm::load(r#"{$call: [list_of, {$ref: integer}]}"#);
        let evaluator = Arc::make_mut(vm.evaluator.as_mut().unwrap());
        evaluator.remaining_work = 2;
        evaluator.limits.max_work = 2;
        vm.run();
        assert_eq!(vm.status(), crate::model::Status::Error);
        assert_eq!(vm.error.as_ref().unwrap().file.as_deref(), Some("std:type_descriptor"));
        assert!(vm.evaluator.as_ref().unwrap().requests.is_empty());
        let inspection = vm.inspect();
        assert!(inspection["objects"].as_hash().unwrap().values()
            .filter_map(|object| object["sources"].as_vec()).flatten()
            .any(|source| source["source"].as_str() == Some("std:type_descriptor")));
    }
}
