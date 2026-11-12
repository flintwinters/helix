//! Block-style YAML output. Helix owns the emitter because the written text is a public contract:
//! a string that would re-read as something else (a number, a boolean, or the head of an expression)
//! must stay quoted, or a serialized state would not round-trip.

use yaml_rust2::Yaml;

pub fn document(value: &Yaml) -> String {
    let mut out = String::new();
    if is_container(value) {
        block(value, 0, &mut out, false);
    } else {
        out.push_str(&scalar(value));
        out.push('\n');
    }
    out
}

/// Lines of a non-empty container at `indent`; `inline` omits the first line's
/// padding when it follows a parent sequence dash.
fn block(value: &Yaml, indent: usize, out: &mut String, inline: bool) {
    let pad = " ".repeat(indent);
    match value {
        Yaml::Hash(entries) => {
            for (position, (key, entry)) in entries.iter().enumerate() {
                if !inline || position != 0 { out.push_str(&pad); }
                out.push_str(&scalar(key));
                out.push(':');
                nested(entry, indent + 2, out);
            }
        }
        Yaml::Array(items) => {
            for (position, item) in items.iter().enumerate() {
                if !inline || position != 0 { out.push_str(&pad); }
                out.push_str("- ");
                if is_container(item) {
                    // The first child line shares the dash: `- a: 1` / `- - 2`.
                    block(item, indent + 2, out, true);
                } else {
                    let text = match item {
                        Yaml::String(s) => string(s, position == 0),
                        other => scalar(other),
                    };
                    out.push_str(&text);
                    out.push('\n');
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
        block(value, indent, out, false);
    } else {
        out.push(' ');
        out.push_str(&scalar(value));
        out.push('\n');
    }
}

fn is_container(value: &Yaml) -> bool {
    matches!(value, Yaml::Hash(h) if !h.is_empty()) || matches!(value, Yaml::Array(a) if !a.is_empty())
}

fn scalar(value: &Yaml) -> String {
    match value {
        Yaml::Integer(i) => i.to_string(),
        Yaml::Real(text) => canonical_real(text).unwrap_or_else(|| text.clone()),
        Yaml::Boolean(b) => b.to_string(),
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
    let reserved = head || KEYWORDS.iter().any(|keyword| text.eq_ignore_ascii_case(keyword));
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
            c if c.is_control() || !is_printable(c) => quoted.push_str(&format!("\\u{:04x}", c as u32)),
            c => quoted.push(c),
        }
    }
    quoted.push('"');
    quoted
}

/// YAML 1.2 printable source characters; other scalar values need escapes.
/// https://yaml.org/spec/1.2.2/#51-character-set
pub(crate) fn is_printable(ch: char) -> bool {
    matches!(ch, '\t' | '\n' | '\r' | ' '..='~' | '\u{85}' | '\u{a0}'..='\u{d7ff}' | '\u{e000}'..='\u{fffd}' | '\u{10000}'..='\u{10ffff}')
}

/// A JSON document for external data pipelines. Reject values JSON cannot represent.
pub fn json_document(value: &Yaml) -> Result<String, &'static str> {
    let mut out = String::new();
    json_value(value, &mut out)?;
    out.push('\n');
    Ok(out)
}

fn json_value(value: &Yaml, out: &mut String) -> Result<(), &'static str> {
    match value {
        Yaml::Null => out.push_str("null"),
        Yaml::Boolean(b) => out.push_str(&b.to_string()),
        Yaml::Integer(i) => out.push_str(&i.to_string()),
        Yaml::Real(text) => {
            out.push_str(&canonical_real(text).ok_or("JSON requires finite numbers")?);
        }
        Yaml::String(text) => out.push_str(&quoted(text)),
        Yaml::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 { out.push(','); }
                json_value(item, out)?;
            }
            out.push(']');
        }
        Yaml::Hash(entries) => {
            out.push('{');
            for (index, (key, item)) in entries.iter().enumerate() {
                let Yaml::String(key) = key else { return Err("JSON object keys must be strings"); };
                if index > 0 { out.push(','); }
                out.push_str(&quoted(key));
                out.push(':');
                json_value(item, out)?;
            }
            out.push('}');
        }
        _ => return Err("unsupported JSON value"),
    }
    Ok(())
}

/// Decimal mantissas and signed exponents work in both YAML 1.1 consumers and JSON.
fn canonical_real(text: &str) -> Option<String> {
    let number = text.parse::<f64>().ok().filter(|n| n.is_finite())?;
    let rendered = format!("{number:?}");
    if let Some((mantissa, exponent)) = rendered.split_once('e') {
        let decimal = if mantissa.contains('.') { "" } else { ".0" };
        let sign = if exponent.starts_with('-') || exponent.starts_with('+') { "" } else { "+" };
        Some(format!("{mantissa}{decimal}e{sign}{exponent}"))
    } else {
        Some(rendered)
    }
}
