//! Type-time preflight: every `type_time` region that can run before the program's first run-time
//! step does, so a failing region stops the run before any effect.
//!
//! The scan covers the program's own source from the root through mapping entries, sequence items
//! and every operand of every form: untaken branches and `let` bindings that are never referenced
//! included, so such a region is checked whether or not the run would reach it. Two boundaries
//! stay out of the direct scan, both because a failure there can be caught or deferred at run time:
//! - a `try` is the explicit catch boundary, so regions inside it keep ordinary order; a `vm`
//!   thunk runs in a child machine whose failure is an observable value, so it is the same;
//! - a function body runs when the function is called and may read the call's arguments.
//! A function body is entered only through a function the scan reaches (a name that resolves to a
//! `fn` in a scanned position, or a `fn` literal passed as an operand), and only its closed
//! regions are hoisted: those that read no parameter, directly or through the bindings they use,
//! so their result does not depend on any call. Open regions run when the call happens and record
//! what they read. Imported modules are not scanned. Evaluation memoizes each root-level region's
//! result for the run; a body region is recomputed by each call.
use super::lexical::Site;
use super::Evaluator;
use crate::builtins::{self, Evaluation, Form};
use crate::graph::{NodeId, Syntax, ROOT};
use std::collections::HashSet;

impl Evaluator {
    /// Qualifying regions: those outside function bodies in document order, then closed regions of
    /// reached function bodies in discovery order.
    pub(super) fn preflight_regions(&self) -> Vec<NodeId> {
        let graph = self.graph();
        let mut regions = Vec::new();
        let mut bodies = vec![ROOT];
        let mut entered = HashSet::new();
        let mut first = true;
        while let Some(start) = bodies.pop() {
            let mut pending = vec![start];
            while let Some(id) = pending.pop() {
                let mut reach = |this: &Self, name: &str, from: NodeId, bodies: &mut Vec<NodeId>| {
                    if let Some(function) = this.function_named(from, name)
                        && entered.insert(function)
                        && let Some(body) = self.body(function)
                    {
                        bodies.push(body);
                    }
                };
                match graph.syntax(id) {
                    Syntax::Mapping(entries) => pending.extend(entries.iter().rev().map(|(_, c)| *c)),
                    Syntax::Sequence(items) => pending.extend(items.iter().rev()),
                    Syntax::Reference(name) => reach(self, name, id, &mut bodies),
                    Syntax::Call(items) => {
                        let head = graph.identifier(items[0]);
                        match head.and_then(builtins::descriptor) {
                            Some(actor) => match actor.evaluation {
                                Evaluation::Form(Form::TypeTime) => {
                                    if first || self.closed(id) {
                                        regions.push(id);
                                    }
                                }
                                Evaluation::Form(Form::Try | Form::Vm | Form::Import | Form::Fn) => {}
                                _ => self.operands(&items[1..], &mut pending, &mut bodies, &mut entered),
                            },
                            None => {
                                if let Some(name) = head {
                                    reach(self, name, items[0], &mut bodies);
                                }
                                self.operands(&items[1..], &mut pending, &mut bodies, &mut entered)
                            }
                        }
                    }
                    _ => {}
                }
            }
            first = false;
        }
        regions
    }
    /// Queue operands; a `fn` literal in operand position is used, so its body is reached.
    fn operands(&self, items: &[NodeId], pending: &mut Vec<NodeId>, bodies: &mut Vec<NodeId>, entered: &mut HashSet<NodeId>) {
        let graph = self.graph();
        for &item in items.iter().rev() {
            match graph.syntax(item) {
                Syntax::Call(_) if graph.form(item) == Some("fn") => {
                    if entered.insert(item)
                        && let Some(body) = self.body(item)
                    {
                        bodies.push(body);
                    }
                }
                _ => pending.push(item),
            }
        }
    }
    fn body(&self, function: NodeId) -> Option<NodeId> {
        match self.graph().syntax(function) {
            Syntax::Call(items) => items.get(2).copied(),
            _ => None,
        }
    }
    /// The `fn` node a lexical name denotes, following dotted keys through maps.
    fn function_named(&self, from: NodeId, name: &str) -> Option<NodeId> {
        let node = self.named_node(from, name)?;
        (self.graph().form(node) == Some("fn")).then_some(node)
    }
    fn named_node(&self, from: NodeId, name: &str) -> Option<NodeId> {
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
    /// A region reads no parameter, directly or through any binding it names: its result is the
    /// same whichever call reaches it.
    fn closed(&self, region: NodeId) -> bool {
        let graph = self.graph();
        let inside = |mut node: NodeId| {
            loop {
                if node == region {
                    return true;
                }
                match graph.parent(node) {
                    Some(parent) => node = parent,
                    None => return false,
                }
            }
        };
        let mut seen = HashSet::new();
        let mut pending = vec![region];
        while let Some(id) = pending.pop() {
            if !seen.insert(id) {
                continue;
            }
            // `Err` for a parameter of a function outside the region, else the binding it reads.
            let mention = |from: NodeId, name: &str| -> Result<Option<NodeId>, ()> {
                let head = name.split('.').next().unwrap_or(name);
                match self.lexical_site(from, head) {
                    Some(Site::Parameter(function, _)) if !inside(function) => Err(()),
                    Some(Site::Entry(target)) => Ok(Some(target)),
                    _ => Ok(None),
                }
            };
            let read = match graph.syntax(id) {
                Syntax::Mapping(entries) => {
                    pending.extend(entries.iter().map(|(_, c)| *c));
                    Ok(None)
                }
                Syntax::Sequence(items) => {
                    pending.extend(items.iter());
                    Ok(None)
                }
                Syntax::Reference(name) => mention(id, name),
                Syntax::Call(items) => {
                    pending.extend(items[1..].iter());
                    match graph.identifier(items[0]) {
                        Some(name) if builtins::arity(name).is_none() => mention(items[0], name),
                        _ => Ok(None),
                    }
                }
                _ => Ok(None),
            };
            match read {
                Ok(target) => pending.extend(target),
                Err(()) => return false,
            }
        }
        true
    }
}
