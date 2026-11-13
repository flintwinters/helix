//! Static effect analysis: whether a function is provably free of effect requests, the property a
//! `pure_function_of` type demands. It reads source only and never runs the function.
//!
//! Everything the function can reach by name is scanned (called or not, so the answer is
//! conservative): its body and annotations, the functions and bindings it mentions, imported
//! modules it names. A function is not provable when the scan finds `effect` or `step`, or a call
//! whose callee it cannot see: a parameter not annotated with a `pure_function_of` type, or a
//! computed callee. Predicates a value carries into `is`/`expect` run when the check does and are
//! not tracked.
use super::lexical::Site;
use super::Evaluator;
use crate::builtins;
use crate::graph::{NodeId, Syntax};
use crate::model::Function;
use std::collections::HashSet;

/// Builtins that call a function operand, by operand position.
fn callees(name: &str) -> &'static [usize] {
    match name {
        "map" | "filter" | "fold" | "each" | "call" => &[1],
        "until" => &[1, 2],
        "try" => &[2],
        _ => &[],
    }
}

impl Evaluator {
    pub(super) fn provably_pure(&self, function: &Function) -> bool {
        let graph = self.graph();
        let mut seen = HashSet::new();
        let mut pending = vec![function.node];
        while let Some(id) = pending.pop() {
            if !seen.insert(id) {
                continue;
            }
            match graph.syntax(id) {
                Syntax::Mapping(entries) => pending.extend(entries.iter().map(|(_, child)| *child)),
                Syntax::Sequence(items) => pending.extend(items.iter()),
                Syntax::Reference(name) => pending.extend(self.named_node(id, name)),
                Syntax::Call(items) => {
                    let head = graph.identifier(items[0]);
                    match head {
                        Some("effect" | "step") => return false,
                        Some("import") => pending.extend(self.program.modules.root_of(id)),
                        _ => {}
                    }
                    let operands = match head.and_then(builtins::descriptor) {
                        Some(actor) => callees(actor.name),
                        None => {
                            pending.extend(head.and_then(|name| self.named_node(items[0], name)));
                            &[0]
                        }
                    };
                    if !operands.iter().all(|&at| items.get(at).is_none_or(|&callee| self.visible_callee(callee, &mut pending))) {
                        return false;
                    }
                    pending.extend(items[1..].iter());
                }
                _ => {}
            }
        }
        true
    }
    /// Whether the function operand at `callee` is one the scan can account for: a literal, a name
    /// of a declaration (queued for scanning) or a parameter promised pure.
    fn visible_callee(&self, callee: NodeId, pending: &mut Vec<NodeId>) -> bool {
        let graph = self.graph();
        let name = match graph.syntax(callee) {
            Syntax::Reference(name) => name,
            Syntax::Call(_) => return graph.form(callee) == Some("fn"),
            _ => match graph.identifier(callee) {
                Some(name) => name,
                None => return false,
            },
        };
        let head = name.split('.').next().unwrap_or(name);
        match self.lexical_site(callee, head) {
            Some(Site::Parameter(function, index)) => self.promises_pure(function, index),
            Some(Site::Entry(_)) => {
                pending.extend(self.named_node(callee, name));
                true
            }
            None => builtins::descriptor(name).is_some_and(|actor| !matches!(actor.name, "effect" | "step")),
        }
    }
    /// A parameter whose annotation is written as a `pure_function_of` type, directly or through a
    /// named declaration.
    fn promises_pure(&self, function: NodeId, index: usize) -> bool {
        let graph = self.graph();
        let declared = Function { program: self.program.clone(), node: function, environment: Vec::new() };
        let Some(&(_, Some(mut annotation))) = self.parameters(&declared).ok().as_ref().and_then(|params| params.get(index)) else {
            return false;
        };
        for _ in 0..8 {
            match graph.syntax(annotation) {
                Syntax::Call(items) => return graph.identifier(items[0]) == Some("pure_function_of"),
                Syntax::Reference(name) => match self.named_node(annotation, name) {
                    Some(target) => annotation = target,
                    None => return false,
                },
                _ => return false,
            }
        }
        false
    }
}
