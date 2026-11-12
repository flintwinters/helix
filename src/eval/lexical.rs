//! Parameter declarations and lexical environments, including free-binding discovery.
use crate::builtins;
use crate::model::{
    Binding, Evaluator, Function, HelixError, Kind, Mode, NodeId, Request, Scope, Value,
    string as s,
};
use std::collections::HashSet;

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
    pub(super) fn parameters(
        &self,
        function: &Function,
    ) -> Result<Vec<(String, Option<NodeId>)>, HelixError> {
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
    pub(super) fn capture_environment(&self, node: NodeId) -> Vec<Scope> {
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

    pub(super) fn resolve(&self, from: NodeId, name: &str) -> Option<Binding> {
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
    pub(super) fn in_function(&self, id: NodeId) -> bool {
        let mut node = self.graph().parent(id);
        while let Some(parent) = node {
            if self.graph().form(parent) == Some("fn") {
                return true;
            }
            node = self.graph().parent(parent)
        }
        false
    }
}
