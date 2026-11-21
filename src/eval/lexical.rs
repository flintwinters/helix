//! Parameter declarations and lexical environments, including free-binding discovery.
use crate::graph::ROOT;
use crate::model::{
    Binding, Evaluator, Function, HelixError, NodeId, Request, Scope, Value, string as s,
};
use crate::graph::{Site, Syntax};
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
        function.program.graph.parameters(function.node)
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

    /// An `unbound` error for `name` at `id`; a missing member below a bound prefix is more
    /// specific than the static name hint.
    pub(super) fn unbound(&self, id: NodeId, name: &str, actor: bool) -> HelixError {
        let mut error = self.graph().unbound(id, name, actor);
        if let Some(hint) = self.unresolved_hint(id, name) {
            error.hint = Some(hint);
        }
        self.phased(error)
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
        self.walk(from, name, false).ok()
    }
    /// Why `name` does not resolve from `from`: the missing member below a bound written prefix,
    /// or `None` when nothing in the name is bound.
    pub(super) fn unresolved_hint(&self, from: NodeId, name: &str) -> Option<String> {
        self.walk(from, name, true).err().flatten()
    }
    /// Resolve `name` from `from`; a failure carries the missing-member hint only when `explain`
    /// asks for it, since ordinary resolution misses are not errors.
    fn walk(&self, from: NodeId, name: &str, explain: bool) -> Result<(Resolution, Option<(NodeId, usize)>), Option<String>> {
        if let Some((binding, frame)) = self.resolve_name(from, name) {
            return Ok((Resolution { binding, members: Vec::new() }, frame));
        }
        let (head, rest) = name.split_once('.').ok_or(None)?;
        let (mut binding, frame) = self.resolve_site(self.graph().root_site(from, head)).ok_or(None)?;
        let parts = rest.split('.').collect::<Vec<_>>();
        // `name[..end]` is the written prefix whose binding is `binding`.
        let mut end = head.len();
        for (index, part) in parts.iter().enumerate() {
            let missing = |found: &dyn Fn() -> Result<Vec<String>, &'static str>| {
                Err(explain.then(|| crate::graph::missing_member(&name[..end], found(), part)))
            };
            binding = match binding {
                Binding::Node(node) => {
                    let node = self.program.modules.root_of(node).unwrap_or(node);
                    let Syntax::Mapping(entries) = self.graph().syntax(node) else {
                        return Ok((Resolution {
                            binding: Binding::Node(node),
                            members: parts[index..].iter().map(|s| (*s).to_string()).collect(),
                        }, frame));
                    };
                    match entries.iter().find(|(k, _)| k == part) {
                        Some((_, id)) => Binding::Node(*id),
                        None => return missing(&|| Ok(entries.iter().map(|(k, _)| k.clone()).collect())),
                    }
                }
                Binding::Value(value) => match &value {
                    Value::Hash(map) => match map.get(&s(part)) {
                        Some(member) => Binding::Value(member.clone()),
                        None => return missing(&|| crate::graph::members_of(&value)),
                    },
                    other => return missing(&|| crate::graph::members_of(other)),
                },
            };
            end += 1 + part.len();
        }
        Ok((Resolution { binding, members: Vec::new() }, frame))
    }
    fn resolve_name(&self, from: NodeId, name: &str) -> Option<(Binding, Option<(NodeId, usize)>)> {
        self.resolve_site(self.graph().lexical_site(from, name))
    }
    fn resolve_site(&self, site: Option<Site>) -> Option<(Binding, Option<(NodeId, usize)>)> {
        Some(match site? {
            Site::Entry(node) => (Binding::Node(node), None),
            Site::Parameter(function, index) => {
                let frame = self.environment.iter().rev().find(|f| f.function == function)?;
                (Binding::Value(frame.args[index].clone()), Some((function, frame.born)))
            }
        })
    }
    /// `Graph::named_node` through the modules the program links.
    pub(super) fn named_node(&self, from: NodeId, name: &str) -> Option<NodeId> {
        self.graph().named_node(from, name, &|node| self.program.modules.root_of(node).unwrap_or(node))
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
