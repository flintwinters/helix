//! Static effect analysis over source: what the code reachable from a node can request. It reads
//! source only and never runs anything. Callable contracts (`pure_function_of`) and non-executing
//! region checks share traversal and callee resolution, with distinct function-body boundaries.
//!
//! Callable contracts scan everything reachable by name (called or not, conservatively): the
//! node and its annotations, the functions and bindings it mentions, imported modules it names. A
//! region check instead enters a function body only when the function is invoked; constructing or
//! storing an effectful callback does not request its effects. Computed callees remain unproven. A
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
        self.scan_effects(start, module, true)
    }

    /// Effects while evaluating a region: constructing a function does not run its body.
    /// Callable purity contracts deliberately retain the stronger transitive scan above.
    pub(crate) fn region_effects(&self, start: NodeId) -> Effects {
        self.scan_effects(start, &|_| None, false)
    }

    fn scan_effects(&self, start: NodeId, module: &dyn Fn(NodeId) -> Option<NodeId>, all_bodies: bool) -> Effects {
        let identity = |node| module(node).unwrap_or(node);
        let mut seen = HashSet::new();
        let mut pending = vec![(start, false)];
        let mut unproven = None;
        while let Some((id, invoked)) = pending.pop() {
            if !seen.insert((id, invoked)) {
                continue;
            }
            match self.syntax(id) {
                Syntax::Mapping(entries) => pending.extend(entries.iter().map(|(_, child)| (*child, false))),
                Syntax::Sequence(items) => pending.extend(items.iter().map(|item| (*item, false))),
                Syntax::Reference(name) => pending.extend(self.named_node(id, name, &identity).map(|node| (node, invoked))),
                Syntax::Call(items) => {
                    let head = self.identifier(items[0]);
                    match head {
                        Some("effect") => return Effects::Requests(id),
                        Some("step") => unproven = unproven.or(Some(id)),
                        Some("import") => pending.extend(module(id).map(|node| (node, false))),
                        _ => {}
                    }
                    let operands = match head.and_then(builtins::descriptor) {
                        Some(actor) => callees(actor.name),
                        None => &[0],
                    };
                    for &at in operands {
                        if let Some(&callee) = items.get(at) {
                            if !self.visible_callee(callee, &identity, &mut pending, all_bodies) {
                                unproven = unproven.or(Some(id));
                            }
                        }
                    }
                    pending.extend(items.iter().enumerate().skip(1)
                        .filter(|(index, _)| head != Some("fn") || *index != 2 || all_bodies || invoked)
                        .map(|(_, node)| (*node, false)));
                }
                _ => {}
            }
        }
        unproven.map_or(Effects::None, Effects::Unproven)
    }

    /// Whether the function operand at `callee` is one the scan can account for: a literal, a name
    /// of a declaration (queued for scanning) or a parameter promised pure.
    fn visible_callee(&self, callee: NodeId, module: &dyn Fn(NodeId) -> NodeId, pending: &mut Vec<(NodeId, bool)>, all_bodies: bool) -> bool {
        let name = match self.syntax(callee) {
            Syntax::Reference(name) => name,
            Syntax::Call(_) => {
                let visible = self.form(callee) == Some("fn");
                if visible {
                    pending.push((callee, true));
                }
                return visible;
            }
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
                    pending.push((target, true));
                    // A computed binding may return a function whose body is not evaluated
                    // during construction. Without tracking that value, invocation is unproved.
                    all_bodies || self.form(target) == Some("fn")
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
