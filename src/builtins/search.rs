//! Bounded regex observations; selection and matching policy live in Helix.
use super::Fault;
use crate::value::Value;
use serde_json::json;
pub(super) fn apply(actor: &str, args: &[Value]) -> Result<Value, Fault> {
    if actor == "source_subtype" {
        let left = super::Ty::parse(&args[0]).map_err(|e| Fault::at(0, "type", e))?;
        let right = super::Ty::parse(&args[1]).map_err(|e| Fault::at(1, "type", e))?;
        return Ok(Value::Boolean(left.below(&right)));
    }
    let Value::String(pattern) = &args[0] else {
        return Err(Fault::expected(0, "string", &args[0]));
    };
    let Value::String(text) = &args[1] else {
        return Err(Fault::expected(1, "string", &args[1]));
    };
    if pattern.len() > 16_384 || text.len() > crate::model::MAX_VALUE_SIZE {
        return Err(Fault::whole("limit", "regex input exceeds bounds".into()));
    }
    let re = regex::RegexBuilder::new(pattern)
        .size_limit(1_000_000)
        .dfa_size_limit(1_000_000)
        .nest_limit(64)
        .build()
        .map_err(|e| Fault::at(0, "regex", e.to_string()))?;
    let mut matches = Vec::new();
    for captures in re.captures_iter(text) {
        if matches.len() >= 4096 {
            return Err(Fault::whole("limit", "regex exceeds 4096 matches".into()));
        }
        matches.push(json!({"span":captures.get(0).map(|m|vec![m.start(),m.end()]),"captures":captures.iter().map(|m|m.map(|m|json!({"text":m.as_str(),"span":[m.start(),m.end()]}))).collect::<Vec<_>>()}));
    }
    crate::value::from_host_yaml(&crate::tooling::source::yaml(&json!(matches)))
        .map_err(|e| Fault::whole("limit", e))
}
