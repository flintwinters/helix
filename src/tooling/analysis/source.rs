//! Non-executing source tools. These passes never link files or construct an evaluator.
use super::body;
use crate::graph::{Graph, Kind, ROOT};
use yaml_rust2::Yaml;

/// The schema covers YAML's data shape; expression semantics belong to the checker.
pub fn schema() -> &'static str {
    include_str!("../../../editor/helix.schema.json")
}

fn object(entries: impl IntoIterator<Item = (&'static str, Yaml)>) -> Yaml {
    Yaml::Hash(
        entries
            .into_iter()
            .map(|(key, value)| (Yaml::String(key.into()), value))
            .collect(),
    )
}

/// Definite failures, as the LSP reports them.
pub(super) fn diagnostics(source: &str) -> Vec<Yaml> {
    analyze(source).0
}

/// Definite failures, then obligations the static fragment could not settle.
fn analyze(source: &str) -> (Vec<Yaml>, Vec<Yaml>) {
    let graph = match Graph::parse(source) {
        Ok(graph) => graph,
        Err(error) => return (vec![error.to_value().plain_yaml().unwrap()], Vec::new()),
    };
    let mut errors = Vec::new();
    let mut pending = vec![ROOT];
    while let Some(id) = pending.pop() {
        match graph.syntax(id) {
            crate::graph::Syntax::Call(items) => {
                let actor = graph.form(id);
                if let Some(name) = actor {
                    if crate::builtins::arity(name).is_none() && graph.lexical_base(id, name).is_none() {
                        errors.push(graph.error(items[0], "unbound", format!("unbound actor `{name}`"))
                            .to_value().plain_yaml().unwrap());
                    }
                }
                if let Some((name, (min, max))) =
                    actor.and_then(|name| crate::builtins::arity(name).map(|arity| (name, arity)))
                {
                    let count = items.len() - 1;
                    if count < min || max.is_some_and(|max| count > max) {
                        let expected = match max {
                            Some(max) if min == max => min.to_string(),
                            Some(max) => format!("{min} to {max}"),
                            None => format!("at least {min}"),
                        };
                        errors.push(
                            graph
                                .error(
                                    id,
                                    "arity",
                                    format!("`{name}` takes {expected} arguments, got {count}"),
                                )
                                .to_value()
                                .plain_yaml()
                                .unwrap(),
                        );
                    }
                }
                // Parameter declarations are syntax, not calls. Annotations and the body
                // are still inspected, including bodies that will never be invoked.
                if actor == Some("fn") {
                    pending.extend(items.iter().skip(2).rev().copied());
                    if let Some(params) = items.get(1) {
                        if let Kind::Seq(params) = graph.kind(*params) {
                            for param in params.iter().rev() {
                                if let Kind::Seq(pair) = graph.kind(*param) {
                                    if let Some(annotation) = pair.get(1) {
                                        pending.push(*annotation);
                                    }
                                }
                            }
                        }
                    }
                } else {
                    pending.extend(items.iter().rev().copied());
                }
            }
            crate::graph::Syntax::Mapping(entries) => {
                pending.extend(entries.iter().rev().map(|(_, id)| *id))
            }
            crate::graph::Syntax::Sequence(items) => pending.extend(items.iter().rev()),
            crate::graph::Syntax::Reference(name)
                if !crate::builtins::is_primitive(name) && graph.lexical_base(id, name).is_none() =>
            {
                errors.push(graph.error(id, "unbound", format!("unbound symbol `{name}`"))
                    .to_value().plain_yaml().unwrap());
            }
            crate::graph::Syntax::Invalid(message) => errors.push(
                graph
                    .error(id, "syntax", message.into())
                    .to_value()
                    .plain_yaml()
                    .unwrap(),
            ),
            _ => {}
        }
    }
    let found = body::check(&graph);
    let yaml = |list: Vec<crate::model::HelixError>| list.into_iter().map(|e| e.to_value().plain_yaml().unwrap());
    errors.extend(yaml(found.errors));
    (errors, yaml(found.unchecked).collect())
}

/// Collect source-local syntax, arity, missing-name and bounded type diagnostics.
/// No expression is run; dynamic member lookup and unsupported types remain unresolved.
pub fn check(source: &str) -> (String, bool) {
    let (errors, unchecked) = analyze(source);
    let valid = errors.is_empty();
    let report = object([
        ("version", Yaml::Integer(1)),
        ("mode", Yaml::String("static".into())),
        (
            "status",
            Yaml::String(if valid { "ok" } else { "error" }.into()),
        ),
        ("diagnostics", Yaml::Array(errors)),
        ("unchecked", Yaml::Array(unchecked)),
    ]);
    (crate::emit::document(&report), valid)
}

/// Reuse the source emitter and refuse any output that changes the parsed program.
/// Comments and scalar presentation are normalized; lexical quoting remains semantic.
pub fn format(source: &str) -> Result<String, String> {
    let graph = Graph::parse(source)
        .map_err(|error| crate::emit::document(&error.to_value().plain_yaml().unwrap()))?;
    let formatted = crate::emit::source(&graph);
    let reparsed = Graph::parse(&formatted)
        .map_err(|_| "source cannot be represented by the canonical formatter\n".to_owned())?;
    if !graph.same_code(ROOT, &reparsed, ROOT) {
        return Err("canonical formatting would change source semantics\n".into());
    }
    Ok(formatted)
}
