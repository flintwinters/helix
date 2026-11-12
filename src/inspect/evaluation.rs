//! Read-only projection of evaluator state and pending continuations.
use crate::model::{
    Actor, Atomic, Evaluator, Function, Graph, Iteration, Mode, NodeId, Operation, Outcome,
    Request, Scope, Value, string as s,
};
use std::sync::Arc;

impl Evaluator {
    pub(crate) fn inspection(&self) -> Value {
        let graph = &self.program.graph;
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
            Self::Iterate(iteration) => iteration.view(g),
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

impl Iteration {
    fn view(&self, graph: &Graph) -> Value {
        object(
            "iterate",
            vec![
                ("site", graph.reference(self.site)),
                ("operation", s(&self.name)),
                (
                    "nodes",
                    Value::Array(self.args.iter().map(|n| graph.reference(*n)).collect()),
                ),
                ("values", Value::Array(self.values.clone().into())),
                ("index", Value::Integer(self.index as i64)),
                ("output", Value::Array(self.output.clone().into())),
                ("state", self.state.clone()),
                ("count", Value::Integer(self.count as i64)),
            ],
        )
    }
}
