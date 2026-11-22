//! A pure builtin expression over literals and parameters completes in one transition.
//! It writes exactly the budget charges and evidence rows the stepwise engine writes, in the same
//! order, so the machine at the next transition boundary is the machine stepwise evaluation
//! reaches there and exports as the same YAML. Anything else (an error, a name that is not a
//! parameter, an engine form, a limit that would trip) abandons the region before any state
//! changes and the stepwise engine evaluates the node, producing its exact errors and traces.
use crate::builtins::{self, Evaluation};
use crate::graph::Syntax;
use crate::model::{Binding, Evaluator, NodeId, Value};

/// One completed node: its value and how many times the stepwise engine charges it
/// (a parameter read is charged when read and again when the reference finishes).
struct Done {
    node: NodeId,
    value: Value,
    charges: usize,
}

impl Evaluator {
    /// Evaluate the call at `id` directly. `true` means the answer is set and nothing was pushed.
    pub(super) fn fast_region(&mut self, id: NodeId) -> bool {
        if self.region.is_some() || self.type_time || self.inherited_type_time {
            return false;
        }
        let mut done = Vec::new();
        let mut units = 0usize;
        let Some(height) = self.pure(id, &mut done, &mut units) else { return false };
        // Root work was charged by `evaluate`; the rest, depth and value budgets must all hold.
        if done.len() - 1 > self.remaining_work
            || self.active.len() + height - 1 >= self.limits.max_eval_depth
            || units > self.remaining_values
        {
            return false;
        }
        self.remaining_work -= done.len() - 1;
        for Done { node, value, charges } in done.iter() {
            for _ in 0..*charges {
                self.charge_value(*node, value).expect("budgets were checked before applying");
            }
            self.note_evaluated(*node, value);
        }
        self.finish(done.pop().expect("a region has a root").value);
        true
    }

    /// The node's value, in post-order into `done`, and the height of its subtree;
    /// `None` when the stepwise engine must evaluate it.
    fn pure(&self, id: NodeId, done: &mut Vec<Done>, units: &mut usize) -> Option<usize> {
        let (value, charges, height) = match self.graph().syntax(id) {
            Syntax::Scalar(yaml) => (Value::from_yaml(yaml), 1, 1),
            // A dotted name projects members, which only the stepwise engine does.
            Syntax::Reference(name) if !name.contains('.') => match self.resolve(id, name) {
                Some(Binding::Value(value)) => (value, 2, 1),
                _ => return None,
            },
            Syntax::Call(items) => {
                let descriptor = self.graph().builtin_call(id)?;
                if !matches!(descriptor.evaluation,
                    Evaluation::Number | Evaluation::Equal | Evaluation::NotEqual | Evaluation::Not)
                    || descriptor.misfit(items.len() - 1).is_some()
                {
                    return None;
                }
                let mut height = 0;
                let mut operands = Vec::with_capacity(items.len() - 1);
                for &item in &items[1..] {
                    height = height.max(self.pure(item, done, units)?);
                    operands.push(done.last()?.value.clone());
                }
                let mut never = |_: builtins::Ask| Err("pending answer".to_string());
                (builtins::apply(descriptor.name, &operands, &mut never).ok()?, 1, height + 1)
            }
            _ => return None,
        };
        let measure = value.measure();
        if measure.height > crate::graph::MAX_NESTING
            || measure.nodes > crate::graph::MAX_NODES
            || measure.units > self.limits.max_value_size
        {
            return None;
        }
        *units += measure.units * charges;
        done.push(Done { node: id, value, charges });
        Some(height)
    }
}
