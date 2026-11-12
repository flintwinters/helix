//! Engine/host handshake: phase gates, request admission and bounded result recording.
use crate::model::{EffectRequest, Evaluator, HelixError, NodeId, Value, string as s};
use yaml_rust2::Yaml;

impl Evaluator {
    pub(super) fn request_effect(
        &mut self,
        site: NodeId,
        nodes: &[NodeId],
        values: &[Value],
    ) -> Result<(), HelixError> {
        if self.type_time || self.inherited_type_time {
            return Err(self.err(site, "purity", "effects are forbidden during type time"));
        }
        if !self.effects_allowed {
            return Err(self.err(site, "effect", "checking cannot execute effects"));
        }
        let Value::String(operation) = &values[0] else {
            return Err(self.err(nodes[0], "type", "expected string effect operation"));
        };
        let payload = values[1]
            .plain_yaml()
            .ok_or_else(|| self.err(nodes[1], "type", "effect payload must be ordinary data"))?;
        let request = EffectRequest {
            id: self.effects.len() as u64 + 1,
            operation: operation.clone(),
            payload,
        };
        self.charge_value(site, &self.effect_view(site, &request))?;
        self.pending_effect = Some((site, request));
        self.answer = None;
        Ok(())
    }
    /// Record that the active outermost region read `name` from the pre-existing frame of
    /// `function`. Type time is pure and cannot reach effects or files, so these values are the
    /// only run-time data its result depends on: source plus these inputs determine it.
    pub(super) fn note_input(&mut self, function: NodeId, born: usize, name: &str, value: &Value) {
        let Some(region) = &self.region else { return };
        if !self.type_time || born >= region.mark {
            return;
        }
        let value = input_view(value);
        let input = Value::Hash(
            [
                (s("function"), self.graph().reference(function)),
                (s("name"), s(name)),
                (s("value"), value),
            ]
            .into_iter()
            .collect(),
        );
        let region = self.region.as_mut().expect("checked above");
        if !region.inputs.contains(&input) {
            region.inputs.push(input);
        }
    }
    /// Leave the outermost region, retaining what it read. A region that read nothing is a pure
    /// function of source and leaves no entry.
    pub(super) fn finish_region(&mut self) -> Result<(), HelixError> {
        let Some(region) = self.region.take() else { return Ok(()) };
        if region.inputs.is_empty() {
            return Ok(());
        }
        let entry = Value::Hash(
            [
                (s("location"), self.graph().reference(region.site)),
                (s("inputs"), Value::Array(region.inputs.into())),
            ]
            .into_iter()
            .collect(),
        );
        if !self.type_inputs.contains(&entry) {
            self.charge_value(region.site, &entry)?;
            self.type_inputs.push(entry);
        }
        Ok(())
    }
    /// The state's compact view of `type_inputs`: each region and the names it read. The values are
    /// retained in the machine and shown in full by inspection.
    pub(crate) fn type_input_names(&self) -> Value {
        let names = |entry: &Value| {
            let Value::Hash(entry) = entry else { return entry.clone() };
            let read = match entry.get(&s("inputs")) {
                Some(Value::Array(inputs)) => inputs
                    .iter()
                    .filter_map(|input| match input {
                        Value::Hash(input) => Some(Value::Hash(
                            ["function", "name"].iter().filter_map(|k| Some((s(k), input.get(&s(k))?.clone()))).collect(),
                        )),
                        _ => None,
                    })
                    .collect::<Vec<_>>(),
                _ => Vec::new(),
            };
            Value::Hash(
                [(s("location"), entry.get(&s("location")).cloned().unwrap_or(Value::Null)), (s("reads"), Value::Array(read.into()))]
                    .into_iter()
                    .collect(),
            )
        };
        Value::Array(self.type_inputs.iter().map(names).collect::<Vec<_>>().into())
    }
    pub fn resolve_effect(&mut self, result: Result<Yaml, (String, String)>) {
        let Some((site, request)) = self.pending_effect.take() else {
            return;
        };
        let mut outcome = match result {
            Ok(data) => crate::value::from_host_yaml(&data)
                .map_err(|why| self.err(site, "type", why))
                .and_then(|value| {
                    self.charge_value(site, &value)?;
                    Ok(value)
                }),
            Err((kind, message)) if kind.len() + message.len() > self.limits.max_value_size => {
                Err(self.err(site, "limit", "effect error exceeds runtime limits"))
            }
            Err((kind, message)) => Err(self.err(site, &kind, message)),
        };
        let mut record = self.effect_view(site, &request);
        if let Value::Hash(fields) = &mut record {
            fields.insert(
                s("outcome"),
                match &outcome {
                    Ok(value) => value.clone(),
                    Err(error) => error.to_value(),
                },
            );
            fields.insert(
                s("status"),
                s(if outcome.is_ok() { "finished" } else { "error" }),
            );
        }
        if let Err(error) = self.charge_value(site, &record) {
            if let Value::Hash(fields) = &mut record {
                fields.insert(s("outcome"), error.to_value());
                fields.insert(s("status"), s("error"));
            }
            outcome = Err(error);
        }
        self.effects.push(record);
        self.answer = Some(outcome);
    }
    pub fn effect_view(&self, site: NodeId, request: &EffectRequest) -> Value {
        Value::Hash(
            [
                (s("id"), Value::Integer(request.id as i64)),
                (s("operation"), s(&request.operation)),
                (s("payload"), Value::from_yaml(&request.payload)),
                (s("location"), self.graph().reference(site)),
            ]
            .into_iter()
            .collect(),
        )
    }
}

/// An input as plain data. A function is identified by its source node: its captured values are
/// recorded as inputs when a region reads them, so code and environment need not be copied.
fn input_view(value: &Value) -> Value {
    match value {
        Value::Function(function) => Value::Hash(
            [(s("function"), function.program.graph.reference(function.node))].into_iter().collect(),
        ),
        Value::Machine(machine) => crate::inspect::machine(machine),
        Value::Array(items) => Value::Array(items.iter().map(input_view).collect::<Vec<_>>().into()),
        Value::Hash(map) => Value::Hash(map.iter().map(|(k, v)| (k.clone(), input_view(v))).collect()),
        other => other.clone(),
    }
}
