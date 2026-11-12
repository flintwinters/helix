//! Strict JSON data conversion with exact signed-integer boundaries.
use crate::builtins::fault::Fault;
use crate::{
    graph,
    value::{Hash, Value},
};

pub(super) fn parse(text: &str) -> Result<Value, Fault> {
    let parsed =
        serde_json::from_str(text).map_err(|error| Fault::at(0, "parse", error.to_string()))?;
    json_data(parsed, 0, &mut 0)
}

fn json_data(value: serde_json::Value, depth: usize, nodes: &mut usize) -> Result<Value, Fault> {
    *nodes += 1;
    if *nodes > graph::MAX_NODES || depth > graph::MAX_NESTING {
        return Err(Fault::at(
            0,
            "limit",
            "JSON data exceeds graph size or nesting limits".into(),
        ));
    }
    Ok(match value {
        serde_json::Value::Null => Value::Null,
        serde_json::Value::Bool(b) => Value::Boolean(b),
        serde_json::Value::String(s) => Value::String(s),
        serde_json::Value::Number(n) => {
            let text = n.to_string();
            if text.contains(['.', 'e', 'E']) {
                let number: f64 = text
                    .parse()
                    .map_err(|_| Fault::at(0, "parse", "invalid JSON number".into()))?;
                if !number.is_finite() {
                    return Err(Fault::at(
                        0,
                        "parse",
                        "JSON number exceeds finite float range".into(),
                    ));
                }
                Value::Float(number)
            } else {
                Value::Integer(text.parse().map_err(|_| {
                    Fault::at(
                        0,
                        "parse",
                        "JSON integer exceeds signed 64-bit range".into(),
                    )
                })?)
            }
        }
        serde_json::Value::Array(items) => Value::Array(
            items
                .into_iter()
                .map(|v| json_data(v, depth + 1, nodes))
                .collect::<Result<_, _>>()?,
        ),
        serde_json::Value::Object(items) => {
            let mut result = Hash::new();
            for (key, value) in items {
                result.insert(Value::String(key), json_data(value, depth + 1, nodes)?);
            }
            Value::Hash(result)
        }
    })
}
