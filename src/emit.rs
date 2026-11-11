//! Block-style YAML output. Helix owns the emitter because the written text is a public contract:
//! a string that would re-read as something else (a number, a boolean, or the head of an expression)
//! must stay quoted, or a serialized state would not round-trip.

use yaml_rust2::Yaml;

pub fn document(value: &Yaml) -> String {
    let mut out = String::new();
    block(value, 0, &mut out);
    out
}

/// Lines of a non-empty container at `indent`; sequence items start with `- `.
fn block(value: &Yaml, indent: usize, out: &mut String) {
    let pad = " ".repeat(indent);
    match value {
        Yaml::Hash(entries) => {
            for (key, entry) in entries {
                out.push_str(&format!("{pad}{}:", scalar(key)));
                nested(entry, indent + 2, out);
            }
        }
        Yaml::Array(items) => {
            for (position, item) in items.iter().enumerate() {
                if is_container(item) {
                    let mut child = String::new();
                    block(item, indent + 2, &mut child);
                    // The first child line shares the dash: `- a: 1` / `- - 2`.
                    out.push_str(&format!("{pad}- {}", &child[indent + 2..]));
                } else {
                    let text = match item {
                        Yaml::String(s) => string(s, position == 0),
                        other => scalar(other),
                    };
                    out.push_str(&format!("{pad}- {text}\n"));
                }
            }
        }
        _ => unreachable!("only containers are emitted as blocks"),
    }
}

/// The value after `key:`: inline for scalars and empty containers, an indented block otherwise.
fn nested(value: &Yaml, indent: usize, out: &mut String) {
    if is_container(value) {
        out.push('\n');
        block(value, indent, out);
    } else {
        out.push_str(&format!(" {}\n", scalar(value)));
    }
}

fn is_container(value: &Yaml) -> bool {
    matches!(value, Yaml::Hash(h) if !h.is_empty()) || matches!(value, Yaml::Array(a) if !a.is_empty())
}

fn scalar(value: &Yaml) -> String {
    match value {
        Yaml::Integer(i) => i.to_string(),
        Yaml::Real(text) => text.clone(),
        Yaml::Boolean(b) => b.to_string(),
        Yaml::Null => "~".to_string(),
        Yaml::String(s) => string(s, false),
        Yaml::Array(_) => "[]".to_string(),
        Yaml::Hash(_) => "{}".to_string(),
        _ => "~".to_string(),
    }
}

/// Plain only when unambiguous: identifier-like text that is not a YAML keyword and not the first
/// item of a sequence (`head`), where it could re-read as an expression naming a builtin or any
/// user function in scope, which the emitter cannot see.
fn string(text: &str, head: bool) -> String {
    const KEYWORDS: [&str; 10] = ["true", "false", "null", "yes", "no", "on", "off", "y", "n", "~"];
    let plain_start = text.chars().next().is_some_and(|c| c.is_ascii_alphabetic() || c == '_' || c == '/');
    let plain_rest = text.chars().all(|c| c.is_ascii_alphanumeric() || " _./@()+=-".contains(c));
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
