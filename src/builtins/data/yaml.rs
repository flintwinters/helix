//! Data-only YAML conversion using the same syntax and emitter as persisted VM state.

use crate::value::{Hash, Value};

use crate::{
    emit,
    graph::{Graph, Kind, NodeId, ROOT},
};

use crate::builtins::fault::Fault;

pub(super) fn parse(text: &str) -> Result<Value, Fault> {
    let graph = Graph::parse_data(source(text)?).map_err(|error| {
        Fault::at(
            0,
            "parse",
            format!(
                "{} (YAML line {}, column {})",
                error.message, error.span.line, error.span.col
            ),
        )
    })?;
    Ok(data(&graph, ROOT))
}

/// Read syntax without resolving symbols, evaluating lists, or linking imports.
fn data(graph: &Graph, id: NodeId) -> Value {
    match graph.kind(id) {
        Kind::Scalar { value, .. } => Value::from_yaml(value),
        Kind::Seq(items) => Value::Array(items.iter().map(|&item| data(graph, item)).collect()),
        Kind::Map(entries) => {
            let mut result = Hash::new();
            for (key, value) in entries {
                result.insert(Value::String(key.clone()), data(graph, *value));
            }
            Value::Hash(result)
        }
    }
}

/// yaml-rust2 leaves BOM handling and printable-input validation to its caller.
/// Escaped controls remain valid: only literal characters in the source are checked.
fn source(text: &str) -> Result<&str, Fault> {
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    let (mut line, mut column, mut carriage_return) = (1, 1, false);
    for ch in text.chars() {
        if !emit::is_printable(ch) {
            return Err(Fault::at(
                0,
                "parse",
                format!(
                    "non-printable character U+{:04X} (YAML line {line}, column {column})",
                    ch as u32
                ),
            ));
        }
        match ch {
            '\r' => {
                line += 1;
                column = 1;
            }
            '\n' => {
                if !carriage_return {
                    line += 1;
                }
                column = 1;
            }
            _ => column += 1,
        }
        carriage_return = ch == '\r';
    }
    Ok(text)
}
