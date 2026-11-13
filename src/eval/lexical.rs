//! Parameter declarations and lexical environments, including free-binding discovery.
use crate::graph::ROOT;
use crate::model::{
    Binding, Evaluator, Function, HelixError, Kind, NodeId, Request, Scope, Value, string as s,
};
use crate::graph::Syntax;
use std::collections::HashSet;

/// A lexical binding plus members that require its computed value.
pub(super) struct Resolution {
    pub(super) binding: Binding,
    pub(super) members: Vec<String>,
}

impl Function {
    /// Free lexical bindings remain lazy code references. Recursive bindings form graph edges,
    /// not recursively expanded source or mutable environment cells.
    pub(crate) fn free_bindings(&self, node: NodeId) -> Vec<(String, Binding)> {
        let evaluator = Evaluator::start(
            self.program.clone(),
            self.environment.clone(),
            Request::Eval(node),
        );
        let graph = &self.program.graph;
        let mut found = Vec::new();
        let mut pending = vec![(node, HashSet::<String>::new(), true)];
        while let Some((id, mut locals, symbolic)) = pending.pop() {
            match graph.syntax(id) {
                Syntax::Call(items) if graph.form(id) == Some("fn") => {
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
                Syntax::Call(items) => {
                    let head = graph.identifier(items[0]);
                    pending.extend(
                        items
                            .iter()
                            .skip(1)
                            .rev()
                            .map(|child| (*child, locals.clone(), false)),
                    );
                    if head.is_some_and(|name| crate::builtins::arity(name).is_none()) {
                        pending.push((items[0], locals, true));
                    } else if head.is_none() {
                        pending.push((items[0], locals, false));
                    }
                }
                Syntax::Sequence(items) => pending.extend(
                    items
                        .iter()
                        .rev()
                        .map(|child| (*child, locals.clone(), false)),
                ),
                Syntax::Mapping(entries) => pending.extend(
                    entries
                        .iter()
                        .rev()
                        .map(|(_, child)| (*child, locals.clone(), false)),
                ),
                Syntax::Reference(_) | Syntax::Scalar(_)
                    if symbolic || matches!(graph.syntax(id), Syntax::Reference(_)) =>
                {
                    let name = match graph.syntax(id) {
                        Syntax::Reference(name) => name,
                        _ => {
                            let Some(name) = graph.identifier(id) else {
                                continue;
                            };
                            name
                        }
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
/// Where a lexical name is declared.
pub(super) enum Site {
    Entry(NodeId),
    Parameter(NodeId, usize),
}
impl Evaluator {
    pub fn check_nodes(&self) -> Vec<NodeId> {
        let mut pending = vec![ROOT];
        let mut nodes = Vec::new();
        while let Some(id) = pending.pop() {
            match self.graph().syntax(id) {
                Syntax::Mapping(entries) => {
                    pending.extend(entries.iter().rev().map(|(_, child)| *child));
                }
                Syntax::Sequence(items) => pending.extend(items.iter().rev()),
                _ => nodes.push(id),
            }
        }
        nodes
    }

    pub(super) fn parameters(
        &self,
        function: &Function,
    ) -> Result<Vec<(String, Option<NodeId>)>, HelixError> {
        let graph = &function.program.graph;
        let id = function.node;
        let Some(items) = graph.call(id) else {
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
                    graph.identifier(pair[0]).ok_or_else(malformed)?.into(),
                    Some(pair[1]),
                )),
                _ => Ok((graph.identifier(p).ok_or_else(malformed)?.into(), None)),
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
        // A computed member captures its base expression. The full name remains
        // the free-binding key, so equality retains both code and member identity.
        self.resolve_frame(from, name).map(|(found, _)| found.binding)
    }
    /// Resolve and, inside an outermost type-time region, record a value read from a frame that
    /// predates it. Parameters and captured values are the only way run-time data can enter.
    pub(super) fn resolve_input(&mut self, from: NodeId, name: &str) -> Option<Resolution> {
        let (found, frame) = self.resolve_frame(from, name)?;
        if let (Binding::Value(value), Some((function, born))) = (&found.binding, frame) {
            self.note_input(function, born, name, value);
        }
        Some(found)
    }
    /// The binding and, for a parameter, the frame (function node, birth) it was read from.
    fn resolve_frame(&self, from: NodeId, name: &str) -> Option<(Resolution, Option<(NodeId, usize)>)> {
        if let Some((binding, frame)) = self.resolve_name(from, name) {
            return Some((Resolution { binding, members: Vec::new() }, frame));
        }
        let (head, rest) = name.split_once('.')?;
        let (mut binding, frame) = self.resolve_name(from, head)?;
        let parts = rest.split('.').collect::<Vec<_>>();
        for (index, part) in parts.iter().enumerate() {
            binding = match binding {
                Binding::Node(node) => {
                    let node = self.program.modules.root_of(node).unwrap_or(node);
                    let Syntax::Mapping(entries) = self.graph().syntax(node) else {
                        return Some((Resolution {
                            binding: Binding::Node(node),
                            members: parts[index..].iter().map(|s| (*s).to_string()).collect(),
                        }, frame));
                    };
                    Binding::Node(entries.iter().find(|(k, _)| k == part)?.1)
                }
                Binding::Value(Value::Hash(map)) => Binding::Value(map.get(&s(part))?.clone()),
                _ => return None,
            };
        }
        Some((Resolution { binding, members: Vec::new() }, frame))
    }
    fn resolve_name(&self, from: NodeId, name: &str) -> Option<(Binding, Option<(NodeId, usize)>)> {
        Some(match self.lexical_site(from, name)? {
            Site::Entry(node) => (Binding::Node(node), None),
            Site::Parameter(function, index) => {
                let frame = self.environment.iter().rev().find(|f| f.function == function)?;
                (Binding::Value(frame.args[index].clone()), Some((function, frame.born)))
            }
        })
    }
    /// The innermost lexical declaration of `name` visible from `from`, independent of any frame.
    pub(super) fn lexical_site(&self, from: NodeId, name: &str) -> Option<Site> {
        let entry = |entries: &[(String, NodeId)]| {
            entries.iter().find(|(k, _)| k == name).map(|(_, id)| Site::Entry(*id))
        };
        let mut child = from;
        while let Some(id) = self.graph().lexical_parent(child) {
            let found = match self.graph().syntax(id) {
                Syntax::Mapping(entries) => entry(entries),
                Syntax::Call(items) if items.get(2) == Some(&child) => {
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
                                .map(|index| Site::Parameter(id, index))
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
    /// The declaration node a possibly dotted lexical name denotes, following keys through maps
    /// and imported modules; `None` for parameters and unresolved names.
    pub(super) fn named_node(&self, from: NodeId, name: &str) -> Option<NodeId> {
        let (head, rest) = match name.split_once('.') {
            Some((head, rest)) => (head, Some(rest)),
            None => (name, None),
        };
        let Some(Site::Entry(mut node)) = self.lexical_site(from, head) else {
            return None;
        };
        for part in rest.into_iter().flat_map(|rest| rest.split('.')) {
            node = self.program.modules.root_of(node).unwrap_or(node);
            let Syntax::Mapping(entries) = self.graph().syntax(node) else {
                return None;
            };
            node = entries.iter().find(|(key, _)| key == part)?.1;
        }
        Some(node)
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
