//! Block-style YAML output. Helix owns the emitter because the written text is a public contract:
//! a string that would re-read as something else (a number, a boolean, or the head of an expression)
//! must stay quoted, or a serialized state would not round-trip.

use yaml_rust2::Yaml;

/// Boundary rendering nodes keep source tokens separate from YAML numeric values.
enum Encoded {
    Scalar(String),
    List(Vec<Encoded>),
    Map(Vec<(String, Encoded)>),
}
impl Encoded {
    fn value(value: &Yaml, head: bool) -> Self {
        match value {
            Yaml::Array(items) => Self::List(
                items
                    .iter()
                    .enumerate()
                    .map(|(i, v)| Self::value(v, i == 0))
                    .collect(),
            ),
            Yaml::Hash(entries) => Self::Map(
                entries
                    .iter()
                    .map(|(k, v)| (scalar(k, false), Self::value(v, false)))
                    .collect(),
            ),
            other => Self::Scalar(scalar(other, head)),
        }
    }
    fn source(graph: &crate::model::Graph, id: crate::model::NodeId) -> Self {
        use crate::model::Kind;
        match graph.kind(id) {
            Kind::Scalar {
                value: Yaml::String(text),
                plain,
            } => Self::Scalar(if *plain { text.clone() } else { quoted(text) }),
            Kind::Scalar { value, .. } => Self::value(value, false),
            Kind::Seq(items) => {
                Self::List(items.iter().map(|id| Self::source(graph, *id)).collect())
            }
            Kind::Map(entries) => Self::Map(
                entries
                    .iter()
                    .map(|(key, id)| (string(key, false), Self::source(graph, *id)))
                    .collect(),
            ),
        }
    }
    fn container(&self) -> bool {
        match self {
            Self::List(items) => !items.is_empty(),
            Self::Map(entries) => !entries.is_empty(),
            _ => false,
        }
    }
    fn inline(&self) -> &str {
        match self {
            Self::Scalar(text) => text,
            Self::List(_) => "[]",
            Self::Map(_) => "{}",
        }
    }
    fn nested(&self, indent: usize, out: &mut String) {
        if self.container() {
            out.push('\n');
            self.block(indent, out)
        } else {
            out.push_str(&format!(" {}\n", self.inline()));
        }
    }
    fn block(&self, indent: usize, out: &mut String) {
        let pad = " ".repeat(indent);
        match self {
            Self::Map(entries) => {
                for (key, value) in entries {
                    out.push_str(&format!("{pad}{key}:"));
                    value.nested(indent + 2, out);
                }
            }
            Self::List(items) => {
                for item in items {
                    if item.container() {
                        let mut child = String::new();
                        item.block(indent + 2, &mut child);
                        out.push_str(&format!("{pad}- {}", &child[indent + 2..]));
                    } else {
                        out.push_str(&format!("{pad}- {}\n", item.inline()));
                    }
                }
            }
            _ => unreachable!("only nonempty containers are block encoded"),
        }
    }
    fn document(&self) -> String {
        let mut out = String::new();
        if self.container() {
            self.block(0, &mut out)
        } else {
            out.push_str(self.inline());
            out.push('\n');
        }
        out
    }
}

pub fn document(value: &Yaml) -> String {
    Encoded::value(value, false).document()
}

/// Pure source recovery freezes only ordinary-data root fields. Runtime objects are retried
/// from source, since inspection documents deliberately cannot be restored as executable values.
pub(crate) fn recovery(
    graph: &crate::model::Graph,
    memo: &std::collections::HashMap<crate::model::NodeId, crate::model::Value>,
) -> String {
    use crate::model::{Kind, Value};
    let encode = |id| {
        memo.get(&id)
            .and_then(Value::plain_yaml)
            .map(|v| Encoded::value(&v, false))
            .unwrap_or_else(|| Encoded::source(graph, id))
    };
    match graph.kind(crate::model::ROOT) {
        Kind::Map(entries) => Encoded::Map(
            entries
                .iter()
                .map(|(name, id)| (string(name, false), encode(*id)))
                .collect(),
        )
        .document(),
        _ => encode(crate::model::ROOT).document(),
    }
}
fn scalar(value: &Yaml, head: bool) -> String {
    match value {
        Yaml::Integer(i) => i.to_string(),
        Yaml::Real(text) => text.clone(),
        Yaml::Boolean(b) => b.to_string(),
        Yaml::String(text) => string(text, head),
        Yaml::Array(_) => "[]".into(),
        Yaml::Hash(_) => "{}".into(),
        _ => "~".into(),
    }
}

/// Plain only when unambiguous: identifier-like text that is not a YAML keyword and not the first
/// item of a sequence (`head`), where it could re-read as an expression naming a builtin or any
/// user function in scope, which the emitter cannot see.
fn string(text: &str, head: bool) -> String {
    const KEYWORDS: [&str; 10] = [
        "true", "false", "null", "yes", "no", "on", "off", "y", "n", "~",
    ];
    let plain_start = text
        .chars()
        .next()
        .is_some_and(|c| c.is_ascii_alphabetic() || c == '_' || c == '/');
    let plain_rest = text
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || " _./@()+=-".contains(c));
    let reserved = head || KEYWORDS.contains(&text.to_ascii_lowercase().as_str());
    if plain_start && plain_rest && !text.ends_with(' ') && !reserved {
        return text.to_string();
    }
    quoted(text)
}

/// Double-quoted text that re-reads as a literal string.
pub(crate) fn quoted(text: &str) -> String {
    let mut quoted = String::from("\"");
    for c in text.chars() {
        match c {
            '"' => quoted.push_str("\\\""),
            '\\' => quoted.push_str("\\\\"),
            '\n' => quoted.push_str("\\n"),
            '\t' => quoted.push_str("\\t"),
            '\r' => quoted.push_str("\\r"),
            c if c.is_control() => quoted.push_str(&format!("\\u{:04x}", c as u32)),
            c => quoted.push(c),
        }
    }
    quoted.push('"');
    quoted
}
