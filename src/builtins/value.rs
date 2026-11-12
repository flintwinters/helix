//! Value inspection shared by every builtin family: type names, numeric views and equality.

use crate::value::Value;

/// Structural equality; numbers compare by value across `int` and `float`.
pub(super) fn equal(a: &Value, b: &Value) -> bool {
    a == b
}

pub(super) fn as_f64(value: &Value) -> Option<f64> {
    match value {
        Value::Integer(i) => Some(*i as f64),
        Value::Float(value) => Some(*value),
        _ => None,
    }
}

pub(super) fn type_name(v: &Value) -> &'static str {
    match v {
        Value::Integer(_) => "integer",
        Value::Float(_) => "float",
        Value::String(_) => "string",
        Value::Boolean(_) => "boolean",
        Value::Array(_) => "list",
        Value::Hash(_) => "map",
        Value::Null => "null",
        Value::Machine(_) => "vm",
        Value::Function(_) => "function",
    }
}

/// Runtime functions are a distinct variant; data can never forge one by its list shape.
pub fn is_function(value: &Value) -> bool {
    matches!(value, Value::Function(_))
}
