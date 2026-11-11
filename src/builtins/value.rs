//! Value inspection shared by every builtin family: type names, numeric views and equality.

use yaml_rust2::Yaml;

/// Structural equality; numbers compare by value across `int` and `float`.
pub(super) fn equal(a: &Yaml, b: &Yaml) -> bool {
    match (a, b) {
        (Yaml::Integer(x), Yaml::Integer(y)) => x == y,
        (Yaml::Array(x), Yaml::Array(y)) => x.len() == y.len() && x.iter().zip(y).all(|(p, q)| equal(p, q)),
        (Yaml::Hash(x), Yaml::Hash(y)) => x.len() == y.len() && x.iter().all(|(k, v)| y.get(k).is_some_and(|w| equal(v, w))),
        _ => match (as_f64(a), as_f64(b)) {
            (Some(x), Some(y)) => x == y,
            _ => a == b,
        },
    }
}

pub(super) fn as_f64(value: &Yaml) -> Option<f64> {
    match value {
        Yaml::Integer(i) => Some(*i as f64),
        Yaml::Real(text) => text.parse().ok(),
        _ => None,
    }
}

pub(super) fn type_name(v: &Yaml) -> &'static str {
    match v {
        Yaml::Integer(_) => "integer",
        Yaml::Real(_) => "float",
        Yaml::String(_) => "string",
        Yaml::Boolean(_) => "boolean",
        Yaml::Array(_) => "list",
        Yaml::Hash(_) => "map",
        Yaml::Null => "null",
        _ => "invalid value",
    }
}
