//! Data-only interchange codecs. YAML and JSON share the established output boundaries.
use super::fault::Fault;
use crate::{
    emit, graph,
    value::{Hash, Value},
};

pub(super) fn apply(actor: &str, value: &Value) -> Result<Value, Fault> {
    match actor {
        "json_dump" => {
            let data = value
                .plain_yaml()
                .ok_or_else(|| Fault::expected(0, "ordinary data", value))?;
            emit::json_document(&data)
                .map(Value::String)
                .map_err(|message| Fault::at(0, "type", message.into()))
        }
        "csv_dump" => csv_dump(value).map(Value::String),
        _ => {
            let Value::String(text) = value else {
                return Err(Fault::expected(0, "string", value));
            };
            if actor == "csv_parse" {
                return csv_parse(text);
            }
            let parsed = serde_json::from_str(text)
                .map_err(|error| Fault::at(0, "parse", error.to_string()))?;
            json_data(parsed, 0, &mut 0)
        }
    }
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

/// CSV is rows of strings, without a header or implicit type conversion. Quoting follows
/// RFC 4180; LF line endings and ragged rows are accepted for everyday interchange.
fn csv_parse(text: &str) -> Result<Value, Fault> {
    let mut rows = Vec::new();
    let mut row = Vec::new();
    let mut field = String::new();
    let mut chars = text.chars().peekable();
    let (mut quoted, mut closed, mut started) = (false, false, false);
    let mut fields = 0;
    while let Some(ch) = chars.next() {
        started = true;
        if quoted {
            if ch == '"' {
                if chars.peek() == Some(&'"') {
                    chars.next();
                    field.push('"');
                } else {
                    quoted = false;
                    closed = true;
                }
            } else {
                field.push(ch);
            }
            continue;
        }
        match ch {
            '"' if field.is_empty() && !closed => quoted = true,
            ',' | '\n' | '\r' => {
                row.push(Value::String(std::mem::take(&mut field)));
                fields += 1;
                if fields + rows.len() > graph::MAX_NODES {
                    return Err(Fault::at(
                        0,
                        "limit",
                        "CSV data exceeds graph size limit".into(),
                    ));
                }
                closed = false;
                if ch != ',' {
                    if ch == '\r' && chars.peek() == Some(&'\n') {
                        chars.next();
                    }
                    rows.push(Value::Array(std::mem::take(&mut row).into()));
                    started = false;
                }
            }
            _ if closed || ch == '"' => {
                return Err(Fault::at(
                    0,
                    "parse",
                    "unexpected character outside a quoted CSV field".into(),
                ))
            }
            _ => field.push(ch),
        }
    }
    if quoted {
        return Err(Fault::at(
            0,
            "parse",
            "unterminated quoted CSV field".into(),
        ));
    }
    if started {
        row.push(Value::String(field));
        rows.push(Value::Array(row.into()));
    }
    Ok(Value::Array(rows.into()))
}

fn csv_dump(value: &Value) -> Result<String, Fault> {
    let Value::Array(rows) = value else {
        return Err(Fault::expected(0, "list of CSV rows", value));
    };
    let mut output = String::new();
    for row in rows {
        let Value::Array(fields) = row else {
            return Err(Fault::expected(0, "list of CSV rows", row));
        };
        if fields.is_empty() {
            return Err(Fault::at(
                0,
                "type",
                "CSV rows must contain at least one string field".into(),
            ));
        }
        for (index, field) in fields.iter().enumerate() {
            let Value::String(text) = field else {
                return Err(Fault::expected(0, "string CSV field", field));
            };
            if index != 0 {
                output.push(',');
            }
            if text.contains([',', '"', '\r', '\n']) || text.is_empty() {
                output.push('"');
                output.push_str(&text.replace('"', "\"\""));
                output.push('"');
            } else {
                output.push_str(text);
            }
        }
        output.push_str("\r\n");
    }
    Ok(output)
}
