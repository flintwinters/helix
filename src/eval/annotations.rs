//! Bounded ordinary-data annotation and evaluated-value evidence.
use crate::model::{Evaluator, NodeId, string as s};
use crate::value::Value;

// Evidence is supplemental to the result. A separate small history budget prevents
// annotated library loops from dominating otherwise compact saved states.
const MAX_ANNOTATION_UNITS: usize = 16_384;

impl Evaluator {
    pub(crate) fn validate_evaluated(&mut self, mut resolve: impl FnMut(&Value) -> Result<NodeId, String>) -> Result<(), String> {
        let limit = self.limits.max_value_size.min(MAX_ANNOTATION_UNITS);
        let mut remaining = limit;
        let graph = &self.program.graph;
        self.evaluated_index = Default::default();
        for (index, row) in self.evaluated.iter().enumerate() {
            let Value::Hash(fields) = row else { return Err("invalid evaluated-value row".into()); };
            let allowed = ["location", "line", "column", "value", "runtime_values_omitted"];
            if fields.len() < 4 || fields.iter().any(|(key, _)| !allowed.iter().any(|name| key == &s(name))) {
                return Err("invalid evaluated-value fields".into());
            }
            let location = fields.get(&s("location")).ok_or("missing evaluated-value location")?;
            let node = resolve(location)?;
            if self.evaluated_index.locations.insert(graph.location(node), index).is_some() {
                return Err("duplicate evaluated-value location".into());
            }
            let span = graph.span(node);
            if fields.get(&s("line")) != Some(&Value::Integer(span.line as i64))
                || fields.get(&s("column")) != Some(&Value::Integer(span.col as i64))
                || !fields.contains_key(&s("value"))
                || fields.get(&s("runtime_values_omitted")).is_some_and(|value| value != &Value::Boolean(true)) {
                return Err("invalid evaluated-value source evidence".into());
            }
            let before = remaining;
            let mut omitted = false;
            if projection(row, &mut remaining, 0, &mut omitted).is_none() || omitted {
                return Err("invalid evaluated-value evidence".into());
            }
            self.evaluated_index.units.push(before - remaining);
            self.evaluated_index.nodes.insert((std::sync::Arc::as_ptr(&self.program) as usize, node), (index, self.program.clone()));
        }
        if limit - remaining != self.evaluated_units {
            return Err("invalid evaluated-value evidence budget".into());
        }
        Ok(())
    }

    /// Keep the latest successful result at each source location, not a call trace.
    pub(super) fn note_evaluated(&mut self, node: NodeId, value: &Value) {
        let identity = (std::sync::Arc::as_ptr(&self.program) as usize, node);
        let limit = self.limits.max_value_size.min(MAX_ANNOTATION_UNITS);
        let cached = self.evaluated_index.nodes.get(&identity).map(|(index, _)| *index);
        // Reserve small-row headroom. Once nearly full, avoid source-path
        // traversal for new nodes; existing entries still update.
        if cached.is_none() && limit.saturating_sub(self.evaluated_units) < 64 {
            self.evaluated_truncated = true;
            return;
        }
        // A refused node has no row; its row costs its value plus a known overhead.
        if let Some((overhead, _)) = self.evaluated_index.absent.get(&identity) {
            let mut remaining = (limit - self.evaluated_units).saturating_sub(*overhead);
            if projection(value, &mut remaining, 1, &mut false).is_none() {
                self.evaluated_truncated = true;
                return;
            }
        }
        let graph = &self.program.graph;
        let (index, key) = match cached {
            Some(index) => (Some(index), None),
            None => {
                let key = graph.location(node);
                (self.evaluated_index.locations.get(&key).copied(), Some(key))
            }
        };
        let location = match (index, &key) {
            (Some(index), _) => match &self.evaluated[index] {
                Value::Hash(fields) => fields.get(&s("location")).unwrap().clone(),
                _ => unreachable!(),
            },
            (None, key) => crate::graph::render_location(key.as_ref().unwrap()),
        };
        let old_units = index.map_or(0, |index| self.evaluated_index.units[index]);
        let available = limit.saturating_sub(self.evaluated_units - old_units);
        let mut remaining = available;
        let mut omitted = false;
        let row = projection(&self.evaluated_row(node, location.clone(), value.clone()), &mut remaining, 0, &mut omitted)
            .and_then(|mut row| {
                if omitted {
                    remaining = remaining.checked_sub("runtime_values_omitted".len() + 2)?;
                    if let Value::Hash(fields) = &mut row {
                        fields.insert(s("runtime_values_omitted"), Value::Boolean(true));
                    }
                }
                Some(row)
            });
        let Some(row) = row else {
            self.evaluated_truncated = true;
            if index.is_none() && self.evaluated_index.absent.len() < crate::graph::MAX_NODES {
                let mut overhead = usize::MAX;
                projection(&self.evaluated_row(node, location, Value::Null), &mut overhead, 0, &mut false);
                let overhead = usize::MAX - overhead - 1;
                self.evaluated_index.absent.insert(identity, (overhead, self.program.clone()));
            }
            return;
        };
        let units = available - remaining;
        self.evaluated_units = self.evaluated_units - old_units + units;
        let index = if let Some(index) = index {
            self.evaluated[index] = row;
            self.evaluated_index.units[index] = units;
            index
        } else {
            self.evaluated.push(row);
            self.evaluated_index.units.push(units);
            self.evaluated_index.locations.insert(key.unwrap(), self.evaluated.len() - 1);
            // Refused nodes may alias the new location.
            self.evaluated_index.absent.clear();
            self.evaluated.len() - 1
        };
        // Programs sharing a location alias one row; bound their identities by the rows.
        let nodes = &mut self.evaluated_index.nodes;
        if cached.is_none() && nodes.len() < self.evaluated.len() {
            nodes.insert(identity, (index, self.program.clone()));
        }
    }

    fn evaluated_row(&self, node: NodeId, location: Value, value: Value) -> Value {
        let span = self.program.graph.span(node);
        Value::Hash([
            (s("location"), location),
            (s("line"), Value::Integer(span.line as i64)),
            (s("column"), Value::Integer(span.col as i64)),
            (s("value"), value),
        ].into_iter().collect())
    }

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
