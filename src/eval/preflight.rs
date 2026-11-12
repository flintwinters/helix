//! Type-time preflight: explicit `type_time` regions that every run evaluates are checked before
//! the program's first run-time step, so a failing region stops the run before any effect.
//!
//! A region qualifies when it sits on an unconditional evaluation path from the root: mapping
//! entries, sequence items, arguments of ordinary calls and iteration forms, a `let` body, the
//! operands of `finally`, and the first operand of `if`, `cond`, `and` and `or`. Regions in a
//! branch, a `try`, a function body or a `let` binding (which is evaluated only if referenced) are
//! not hoisted: they depend on a choice, a handler or call-time arguments, so they keep ordinary
//! evaluation order, `try` can still catch their failures and unchosen branches stay unchecked.
//! Evaluation memoizes each region's result, so the run reuses it.
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
                        Evaluation::Form(Form::Let) => pending.extend(items.get(2)),
                        Evaluation::Form(Form::If | Form::Cond | Form::And | Form::Or) => pending.extend(items.get(1)),
                        Evaluation::Form(Form::Finally | Form::Map | Form::Filter | Form::Fold | Form::Each | Form::Until | Form::Call) => {
                            pending.extend(items[1..].iter().rev())
                        }
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
