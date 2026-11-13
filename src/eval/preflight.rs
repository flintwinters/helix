//! Type-time preflight: every `type_time` region that can run before the program's first run-time
//! step does, so a failing region stops the run before any effect.
//!
//! The scan covers the program's own source from the root through mapping entries, sequence items
//! and every operand of every form: untaken branches and `let` bindings that are never referenced
//! included, so such a region is checked whether or not the run would reach it. Two boundaries stay
//! out of reach, both because a failure there can be caught or deferred at run time:
//! - a `try` is the explicit catch boundary, so regions inside it keep ordinary order;
//! - a function body runs when the function is called, possibly under a `try` at the call site, and
//!   may read the call's arguments; its regions run then and record what they read.
//! Imported modules are not scanned. Evaluation memoizes each region's result for the run.
use super::Evaluator;
use crate::builtins::{self, Evaluation, Form};
use crate::graph::{NodeId, Syntax, ROOT};

impl Evaluator {
    /// Qualifying regions in document order.
    pub(super) fn preflight_regions(&self) -> Vec<NodeId> {
        let graph = self.graph();
        let mut pending = vec![ROOT];
        let mut regions = Vec::new();
        while let Some(id) = pending.pop() {
            match graph.syntax(id) {
                Syntax::Mapping(entries) => pending.extend(entries.iter().rev().map(|(_, child)| *child)),
                Syntax::Sequence(items) => pending.extend(items.iter().rev()),
                Syntax::Call(items) => match graph.identifier(items[0]).and_then(builtins::descriptor) {
                    Some(actor) => match actor.evaluation {
                        Evaluation::Form(Form::TypeTime) => regions.push(id),
                        Evaluation::Form(Form::Try | Form::Import | Form::Fn) => {}
                        _ => pending.extend(items[1..].iter().rev()),
                    },
                    None => pending.extend(items[1..].iter().rev()),
                },
                _ => {}
            }
        }
        regions
    }
}
