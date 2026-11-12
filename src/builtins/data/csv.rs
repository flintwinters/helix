//! CSV rows and fields remain strings, without implicit headers or coercion.
use crate::builtins::fault::Fault;
use crate::{graph, value::Value};

/// CSV is rows of strings, without a header or implicit type conversion. Quoting follows
/// RFC 4180; LF line endings and ragged rows are accepted for everyday interchange.
pub(super) fn parse(text: &str) -> Result<Value, Fault> {
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
                ));
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

pub(super) fn dump(value: &Value) -> Result<String, Fault> {
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
