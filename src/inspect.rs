//! Finite, named object graphs. Inspection never runs the inspected machine.
use crate::model::{Binding, Function, Hash, Program, Value, Vm, string as s};
use std::sync::Arc;

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
    Program(Arc<Program>),
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
    fn program(&mut self, program: Arc<Program>) -> Value {
        let index = self
            .pending
            .iter()
            .position(
                |p| matches!(p,Pending::Program(p) if Arc::ptr_eq(p,&program) || **p==*program),
            )
            .unwrap_or_else(|| {
                self.pending.push(Pending::Program(program));
                self.pending.len() - 1
            });
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
                (s("program"), self.program(function.program.clone())),
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
            fields.insert(s("program"), self.program(evaluator.program.clone()));
        }
        body
    }
    fn complete(mut self, mut root: Value) -> Value {
        let mut index = 0;
        while index < self.pending.len() {
            let body = match self.pending[index].clone() {
                Pending::Runtime(Value::Function(function)) => self.function_body(&function),
                Pending::Runtime(Value::Machine(machine)) => self.machine_body(&machine),
                Pending::Program(program) => {
                    let graph = &program.graph;
                    let mut roots = vec![crate::model::ROOT];
                    roots.extend(program.modules.roots.iter().copied());
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
                _ => unreachable!("only functions, machines and programs are named objects"),
            };
            self.objects.insert(s(&format!("object{index}")), body);
            index += 1;
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
                    .position(|v| matches!(v,Pending::Runtime(v) if v==value))
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
