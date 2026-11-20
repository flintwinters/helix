//! Block-style YAML states and compact source formatting. The written text is a public contract:
//! a string that would re-read as something else (a number or a boolean)
//! must stay quoted, or a serialized state would not round-trip.

use yaml_rust2::Yaml;

/// Boundary rendering nodes keep source tokens separate from YAML numeric values.
enum Encoded {
    Scalar(String),
    Tagged(String, Box<Encoded>),
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
            Kind::Scalar { value, .. } => Self::value(value, false),
            Kind::Seq(items) => {
                Self::List(items.iter().map(|id| Self::source(graph, *id)).collect())
            }
            Kind::Map(entries) => Self::Map(
                entries
                    .iter()
                    .map(|(key, id)| {
                        (
                            if matches!(key.as_str(), "$call" | "$ref" | "$literal") {
                                key.clone()
                            } else {
                                string(key, false)
                            },
                            Self::source(graph, *id),
                        )
                    })
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
            Self::Tagged(..) => unreachable!("tags are source layout only"),
            Self::List(_) => "[]",
            Self::Map(_) => "{}",
        }
    }
    fn write_inline(&self, indent: usize, out: &mut String) {
        // Only source plain scalars contain literal newlines here: quoted strings
        // already use escapes. YAML needs one extra break to avoid folding them.
        let mut after_break = false;
        for ch in self.inline().chars() {
            if ch == '\n' {
                if !after_break {
                    out.push('\n');
                }
                out.push('\n');
                after_break = true;
            } else {
                if after_break {
                    out.push_str(&" ".repeat(indent));
                }
                out.push(ch);
                after_break = false;
            }
        }
    }
    fn nested(&self, indent: usize, out: &mut String) {
        if self.container() {
            out.push('\n');
            self.block(indent, out, false)
        } else {
            out.push(' ');
            self.write_inline(indent, out);
            out.push('\n');
        }
    }
    fn block(&self, indent: usize, out: &mut String, inline: bool) {
        let pad = " ".repeat(indent);
        match self {
            Self::Map(entries) => {
                for (position, (key, value)) in entries.iter().enumerate() {
                    if !inline || position != 0 {
                        out.push_str(&pad);
                    }
                    out.push_str(key);
                    out.push(':');
                    value.nested(indent + 2, out);
                }
            }
            Self::List(items) => {
                for (position, item) in items.iter().enumerate() {
                    if !inline || position != 0 {
                        out.push_str(&pad);
                    }
                    out.push_str("- ");
                    if item.container() {
                        item.block(indent + 2, out, true);
                    } else {
                        item.write_inline(indent + 2, out);
                        out.push('\n');
                    }
                }
            }
            _ => unreachable!("only nonempty containers are block encoded"),
        }
    }
    fn document(&self) -> String {
        let mut out = String::new();
        if self.container() {
            self.block(0, &mut out, false)
        } else {
            self.write_inline(2, &mut out);
            out.push('\n');
        }
        out
    }
}

pub fn document(value: &Yaml) -> String {
    Encoded::value(value, false).document()
}

impl Encoded {
    /// Tags are source sugar only. Literal payloads retain their exact data representation.
    fn tagged(self) -> Self {
        match self {
            Self::Map(mut entries) if entries.len() == 1
                && matches!(entries[0].0.as_str(), "$call" | "$ref" | "$literal") => {
                let (key, value) = entries.pop().unwrap();
                let value = if key == "$literal" { value } else { value.tagged() };
                Self::Tagged(key[1..].to_owned(), Box::new(value))
            }
            Self::Map(entries) => Self::Map(entries.into_iter().map(|(k, v)| (k, v.tagged())).collect()),
            Self::List(items) => Self::List(items.into_iter().map(Self::tagged).collect()),
            other => other,
        }
    }
    /// Source layout keeps short leaf expressions in flow collections.
    /// Runtime state emission deliberately retains its existing block contract.
    fn flow(&self) -> String {
        match self {
            Self::Scalar(text) => text.clone(),
            Self::Tagged(tag, value) => format!("!{tag} {}", value.flow()),
            Self::List(items) => format!(
                "[{}]",
                items.iter().map(Self::flow).collect::<Vec<_>>().join(", ")
            ),
            Self::Map(entries) => format!(
                "{{{}}}",
                entries
                    .iter()
                    .map(|(key, value)| format!("{key}: {}", value.flow()))
                    .collect::<Vec<_>>()
                    .join(", ")
            ),
        }
    }
    /// Structural forms introduce bodies or branches. Keep their boundaries visible,
    /// including when an enclosing expression would otherwise fit on one line.
    fn structural(&self) -> bool {
        match self {
            Self::Map(entries) => {
                let control = entries.len() == 1 && entries[0].0 == "$call"
                    && matches!(&entries[0].1, Self::List(items)
                        if matches!(items.first(), Some(Self::Scalar(actor))
                            if matches!(actor.as_str(), "fn" | "let" | "if" | "try" | "until")));
                control || entries.iter().any(|(_, value)| value.structural())
            }
            Self::List(items) => items.iter().any(Self::structural),
            Self::Tagged(tag, value) => {
                (tag == "call" && matches!(value.as_ref(), Self::List(items)
                    if matches!(items.first(), Some(Self::Scalar(actor))
                        if matches!(actor.as_str(), "fn" | "let" | "if" | "try" | "until"))))
                    || value.structural()
            },
            Self::Scalar(_) => false,
        }
    }
    fn pretty(&self, indent: usize, prefix: &str, out: &mut String) {
        self.layout(indent, prefix, out, false);
    }
    fn layout(&self, indent: usize, prefix: &str, out: &mut String, force_block: bool) {
        if let Self::Tagged(tag, value) = self {
            let tagged_prefix = format!("{prefix}!{tag} ");
            value.layout(indent, &tagged_prefix, out, force_block || self.structural());
            return;
        }
        let flow = self.flow();
        if !self.container()
            || (!force_block && !self.structural()
                && indent + prefix.chars().count() + flow.chars().count() <= 100)
        {
            out.push_str(&" ".repeat(indent));
            out.push_str(prefix);
            out.push_str(&flow);
            out.push('\n');
            return;
        }
        // A sequence's mapping entry can start directly after its dash.
        if prefix == "- " {
            if let Self::Map(entries) = self {
                for (index, (key, value)) in entries.iter().enumerate() {
                    if index == 0 {
                        value.layout(indent, &format!("- {key}: "), out, key == "$call" && self.structural());
                    } else {
                        value.pretty(indent + 2, &format!("{key}: "), out);
                    }
                }
                return;
            }
        }
        let nested = if prefix.is_empty() {
            indent
        } else {
            out.push_str(&" ".repeat(indent));
            out.push_str(prefix.trim_end());
            out.push('\n');
            indent + if prefix.starts_with("- ") && prefix != "- " { 4 } else { 2 }
        };
        match self {
            Self::Map(entries) => {
                for (key, value) in entries {
                    value.layout(nested, &format!("{key}: "), out, key == "$call" && self.structural());
                }
            }
            Self::List(items) => {
                for item in items {
                    item.pretty(nested, "- ", out);
                }
            }
            Self::Scalar(_) | Self::Tagged(..) => unreachable!(),
        }
    }
}

/// Canonical source layout preserves YAML representation values.
pub(crate) fn source(graph: &crate::model::Graph) -> String {
    let mut out = String::new();
    let source = Encoded::source(graph, crate::model::ROOT).tagged();
    match &source {
        Encoded::Map(entries)
            if entries.len() > 1
                || entries.first().is_some_and(|(key, _)| {
                    !matches!(key.as_str(), "$call" | "$ref" | "$literal")
                }) =>
        {
            for (key, value) in entries {
                value.pretty(0, &format!("{key}: "), &mut out);
            }
        }
        _ => source.pretty(0, "", &mut out),
    }
    out
}

/// Pure source recovery freezes only ordinary-data root fields. Runtime objects are retried
/// from source, since inspection documents deliberately cannot be restored as executable values.
pub(crate) fn recovery(
    graph: &crate::model::Graph,
    memo: &std::collections::HashMap<crate::model::NodeId, crate::model::Value>,
) -> String {
    use crate::model::Value;
    let encode = |id| {
        memo.get(&id)
            .and_then(Value::plain_yaml)
            .map(|v| Encoded::Map(vec![(quoted("$literal"), Encoded::value(&v, false))]))
            .unwrap_or_else(|| Encoded::source(graph, id))
    };
    match graph.syntax(crate::model::ROOT) {
        crate::graph::Syntax::Mapping(entries) => Encoded::Map(
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
        Yaml::Real(text) => canonical_real(text).unwrap_or_else(|| text.clone()),
        Yaml::Boolean(b) => b.to_string(),
        Yaml::String(text) => string(text, head),
        Yaml::Array(_) => "[]".into(),
        Yaml::Hash(_) => "{}".into(),
        _ => "~".into(),
    }
}

/// Plain only when the scalar resolves to the same YAML string.
fn string(text: &str, _head: bool) -> String {
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
    let reserved = KEYWORDS
        .iter()
        .any(|keyword| text.eq_ignore_ascii_case(keyword));
    if plain_start && plain_rest && !text.ends_with(' ') && !reserved {
        return text.to_string();
    }
    quoted(text)
}

/// Double-quoted text that re-reads as a literal string.
fn quoted(text: &str) -> String {
    let mut quoted = String::from("\"");
    for c in text.chars() {
        match c {
            '"' => quoted.push_str("\\\""),
            '\\' => quoted.push_str("\\\\"),
            '\n' => quoted.push_str("\\n"),
            '\t' => quoted.push_str("\\t"),
            '\r' => quoted.push_str("\\r"),
            c if c.is_control() || matches!(c, '\u{2028}' | '\u{2029}') || !is_printable(c) => {
                quoted.push_str(&format!("\\u{:04x}", c as u32))
            }
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
                if index > 0 {
                    out.push(',');
                }
                json_value(item, out)?;
            }
            out.push(']');
        }
        Yaml::Hash(entries) => {
            out.push('{');
            for (index, (key, item)) in entries.iter().enumerate() {
                let Yaml::String(key) = key else {
                    return Err("JSON object keys must be strings");
                };
                if index > 0 {
                    out.push(',');
                }
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
        let sign = if exponent.starts_with('-') || exponent.starts_with('+') {
            ""
        } else {
            "+"
        };
        Some(format!("{mantissa}{decimal}e{sign}{exponent}"))
    } else {
        Some(rendered)
    }
}

/// A one-line flow excerpt of a runtime value for error messages, cut near `limit` characters.
/// Strings are always quoted so an excerpt never reads as another type; work stays bounded.
pub(crate) fn excerpt(value: &crate::value::Value, limit: usize) -> String {
    let mut out = String::new();
    excerpt_into(value, limit, &mut out);
    if out.chars().count() > limit {
        out = out.chars().take(limit.saturating_sub(1)).collect();
        out.push('…');
    }
    out
}

fn excerpt_into(value: &crate::value::Value, limit: usize, out: &mut String) {
    use crate::value::Value;
    let full = |out: &String| out.len() > limit;
    match value {
        Value::String(text) => out.push_str(&quoted(&text.chars().take(limit + 1).collect::<String>())),
        Value::Array(items) => {
            out.push('[');
            for (i, item) in items.iter().enumerate() {
                if full(out) { break }
                if i > 0 { out.push_str(", ") }
                excerpt_into(item, limit, out);
            }
            out.push(']');
        }
        Value::Hash(map) => {
            out.push('{');
            for (i, (key, item)) in map.iter().enumerate() {
                if full(out) { break }
                if i > 0 { out.push_str(", ") }
                match key {
                    Value::String(key) => out.push_str(&string(key, false)),
                    other => excerpt_into(other, limit, out),
                }
                out.push_str(": ");
                excerpt_into(item, limit, out);
            }
            out.push('}');
        }
        Value::Function(_) => out.push_str("<function>"),
        Value::Machine(_) => out.push_str("<vm>"),
        leaf => out.push_str(&leaf.plain_yaml().map_or_else(String::new, |yaml| scalar(&yaml, false))),
    }
}
