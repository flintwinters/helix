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
    /// Type time is pure, so only recorded effect outcomes can make its result differ between runs.
    /// An entry that follows completed effects names them; none is recorded for a region that no
    /// outcome precedes, such as every preflighted region.
    pub(super) fn record_type_inputs(&mut self, site: NodeId) {
        if self.effects.is_empty() {
            return;
        }
        let entry = Value::Hash(
            [
                (s("location"), self.graph().reference(site)),
                (s("effects"), Value::Array((1..=self.effects.len() as i64).map(Value::Integer).collect::<Vec<_>>().into())),
            ]
            .into_iter()
            .collect(),
        );
        if self.type_inputs.last() != Some(&entry) {
            self.type_inputs.push(entry);
        }
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
