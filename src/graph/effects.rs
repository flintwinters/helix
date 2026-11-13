//! Static effect analysis over source: what the code reachable from a node can request. It reads
//! source only and never runs anything, so execution (the `pure_function_of` contract) and
//! non-executing analysis (`check`) share one answer.
//!
//! Everything reachable by name is scanned (called or not, so the answer is conservative): the
//! node and its annotations, the functions and bindings it mentions, imported modules it names. A
//! call whose callee the scan cannot see is unproven, never assumed pure: a parameter not annotated
//! with a `pure_function_of` type, or a computed callee. Predicates a value carries into
//! `is`/`expect` run when the check does and are not tracked.
use super::{Graph, NodeId, Site, Syntax};
use crate::builtins;
use std::collections::HashSet;

/// What the code reachable from a node can request.
#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) enum Effects {
    /// Nothing the scan can find requests an effect.
    None,
    /// An `effect` call at this node is reachable.
    Requests(NodeId),
    /// No effect found, but this node calls through something the scan cannot see.
    Unproven(NodeId),
}

/// Builtins that call a function operand, by operand position.
fn callees(name: &str) -> &'static [usize] {
    match name {
        "map" | "filter" | "fold" | "each" | "call" => &[1],
        "until" => &[1, 2],
        "try" => &[2],
        _ => &[],
    }
}

impl Graph {
    /// `module` maps an import node to the root of the module it links, when something links.
    pub(crate) fn effects(&self, start: NodeId, module: &dyn Fn(NodeId) -> Option<NodeId>) -> Effects {
        let identity = |node| module(node).unwrap_or(node);
        let mut seen = HashSet::new();
        let mut pending = vec![start];
        let mut unproven = None;
        while let Some(id) = pending.pop() {
            if !seen.insert(id) {
                continue;
            }
            match self.syntax(id) {
                Syntax::Mapping(entries) => pending.extend(entries.iter().map(|(_, child)| *child)),
                Syntax::Sequence(items) => pending.extend(items.iter()),
                Syntax::Reference(name) => pending.extend(self.named_node(id, name, &identity)),
                Syntax::Call(items) => {
                    let head = self.identifier(items[0]);
                    match head {
                        Some("effect") => return Effects::Requests(id),
                        Some("step") => unproven = unproven.or(Some(id)),
                        Some("import") => pending.extend(module(id)),
                        _ => {}
                    }
                    let operands = match head.and_then(builtins::descriptor) {
                        Some(actor) => callees(actor.name),
                        None => &[0],
                    };
                    for &at in operands {
                        if let Some(&callee) = items.get(at) {
                            if !self.visible_callee(callee, &identity, &mut pending) {
                                unproven = unproven.or(Some(id));
                            }
                        }
                    }
                    pending.extend(items[1..].iter());
                }
                _ => {}
            }
        }
        unproven.map_or(Effects::None, Effects::Unproven)
    }

    /// Whether the function operand at `callee` is one the scan can account for: a literal, a name
    /// of a declaration (queued for scanning) or a parameter promised pure.
    fn visible_callee(&self, callee: NodeId, module: &dyn Fn(NodeId) -> NodeId, pending: &mut Vec<NodeId>) -> bool {
        let name = match self.syntax(callee) {
            Syntax::Reference(name) => name,
            Syntax::Call(_) => return self.form(callee) == Some("fn"),
            _ => match self.identifier(callee) {
                Some(name) => name,
                None => return false,
            },
        };
        let head = name.split('.').next().unwrap_or(name);
        match self.lexical_site(callee, head) {
            Some(Site::Parameter(function, index)) => self.promises_pure(function, index, module),
            Some(Site::Entry(_)) => match self.named_node(callee, name, module) {
                Some(target) => {
                    pending.push(target);
                    true
                }
                // A member of something that does not link, such as an unresolved import.
                None => false,
            },
            None => builtins::descriptor(name).is_some_and(|actor| !matches!(actor.name, "effect" | "step")),
        }
    }

    /// A parameter whose annotation is written as a `pure_function_of` type, directly or through a
    /// named declaration.
    fn promises_pure(&self, function: NodeId, index: usize, module: &dyn Fn(NodeId) -> NodeId) -> bool {
        let Some(&(_, Some(mut annotation))) = self.parameters(function).ok().as_ref().and_then(|params| params.get(index)) else {
            return false;
        };
        for _ in 0..8 {
            match self.syntax(annotation) {
                Syntax::Call(items) => return self.identifier(items[0]) == Some("pure_function_of"),
                Syntax::Reference(name) => match self.named_node(annotation, name, module) {
                    Some(target) => annotation = target,
                    None => return false,
                },
                _ => return false,
            }
        }
        false
    }
}
