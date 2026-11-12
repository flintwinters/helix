//! Non-executing source tools. These passes never link files or construct an evaluator.
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

pub(super) fn diagnostics(source: &str) -> Vec<Yaml> {
    let graph = match Graph::parse(source) {
        Ok(graph) => graph,
        Err(error) => return vec![error.to_value().plain_yaml().unwrap()],
    };
    let mut errors = Vec::new();
    let mut pending = vec![ROOT];
    while let Some(id) = pending.pop() {
        match graph.syntax(id) {
            crate::syntax::Syntax::Call(items) => {
                let actor = graph.form(id);
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
            crate::syntax::Syntax::Mapping(entries) => {
                pending.extend(entries.iter().rev().map(|(_, id)| *id))
            }
            crate::syntax::Syntax::Sequence(items) => pending.extend(items.iter().rev()),
            crate::syntax::Syntax::Invalid(message) => errors.push(
                graph
                    .error(id, "syntax", message.into())
                    .to_value()
                    .plain_yaml()
                    .unwrap(),
            ),
            _ => {}
        }
    }
    errors
}

/// Parse and collect statically known builtin arity errors. No expression is run.
/// Dynamic calls, imports, types and value-dependent failures remain runtime checks.
pub fn check(source: &str) -> (String, bool) {
    let errors = diagnostics(source);
    let valid = errors.is_empty();
    let report = object([
        ("version", Yaml::Integer(1)),
        ("mode", Yaml::String("static".into())),
        (
            "status",
            Yaml::String(if valid { "ok" } else { "error" }.into()),
        ),
        ("diagnostics", Yaml::Array(errors)),
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
