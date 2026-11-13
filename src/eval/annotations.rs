//! Bounded inspection evidence, not executable saved-state restoration.
use crate::model::{Evaluator, NodeId, string as s};
use crate::value::Value;

// Evidence is supplemental to the result. A separate small history budget prevents
// annotated library loops from dominating otherwise compact saved states.
const MAX_ANNOTATION_UNITS: usize = 16_384;

impl Evaluator {
    pub(super) fn note_annotation(&mut self, kind: &str, target: Value, declaration: NodeId, ty: Value, value: Value) {
        if self.annotations_truncated { return; }
        let graph = &self.program.graph;
        let row = Value::Hash([
            (s("kind"), s(kind)),
            (s("target"), target),
            (s("declaration"), Value::Hash([
                (s("location"), graph.reference(declaration)),
                (s("code"), self.program.code[declaration].clone()),
            ].into_iter().collect())),
            (s("type"), ty),
            (s("value"), value),
        ].into_iter().collect());
        let available = self.limits.max_value_size.min(MAX_ANNOTATION_UNITS).saturating_sub(self.annotation_units);
        let mut remaining = available;
        let mut omitted = false;
        let Some(mut row) = projection(&row, &mut remaining, 0, &mut omitted) else {
            self.annotations_truncated = true;
            return;
        };
        if omitted {
            let key = s("runtime_values_omitted");
            let Some(left) = remaining.checked_sub("runtime_values_omitted".len() + 2) else {
                self.annotations_truncated = true;
                return;
            };
            remaining = left;
            if let Value::Hash(fields) = &mut row { fields.insert(key, Value::Boolean(true)); }
        }
        self.annotation_units += available - remaining;
        self.annotations.push(row);
    }
}

/// Ordinary data is copied within both existing shape and size limits. Live
/// objects become shallow summaries: never traverse or retain their environments.
fn projection(value: &Value, remaining: &mut usize, depth: usize, omitted: &mut bool) -> Option<Value> {
    if depth > crate::graph::MAX_NESTING { return None; }
    let summary = match value {
        Value::Function(function) => Some(Value::Hash([
            (s("kind"), s("function")),
            (s("location"), function.program.graph.reference(function.node)),
        ].into_iter().collect())),
        Value::Machine(machine) => Some(Value::Hash([
            (s("kind"), s("vm")), (s("status"), s(machine.status().as_str())),
        ].into_iter().collect())),
        _ => None,
    };
    if let Some(summary) = summary {
        *omitted = true;
        return projection(&summary, remaining, depth, omitted);
    }
    *remaining = remaining.checked_sub(match value { Value::String(text) => 1 + text.len(), _ => 1 })?;
    Some(match value {
        Value::Array(items) => Value::Array(items.iter().map(|item|
            projection(item, remaining, depth + 1, omitted)).collect::<Option<Vec<_>>>()?.into()),
        Value::Hash(fields) => Value::Hash(fields.iter().map(|(key, value)| Some((
            projection(key, remaining, depth + 1, omitted)?,
            projection(value, remaining, depth + 1, omitted)?,
        ))).collect::<Option<Vec<_>>>()?.into_iter().collect()),
        _ => value.clone(),
    })
}
