//! Type-time preflight: explicit `type_time` regions that every run evaluates are checked before
//! the program's first run-time step, so a failing region stops the run before any effect.
//!
//! A region qualifies when it sits on an unconditional evaluation path from the root: mapping
//! entries, sequence items and arguments of non-form calls. Forms (`try`, `if`, `fn`, `let`,
//! iteration, imports...) choose, delay or scope their operands, so regions beneath them are left to
//! ordinary evaluation order, which keeps `try` able to catch a type-time failure and unchosen
//! branches unchecked. Evaluation memoizes each region's result, so the run reuses it.
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
                        Evaluation::Form(_) => {}
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
