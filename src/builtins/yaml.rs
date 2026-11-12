//! Data-only YAML conversion using the same syntax and emitter as persisted VM state.

use yaml_rust2::{yaml::Hash, Yaml};

use crate::{emit, graph::{Graph, Kind, NodeId, ROOT}};

use super::fault::Fault;

pub(super) fn apply(actor: &str, value: &Yaml) -> Result<Yaml, Fault> {
    match actor {
        "yaml_parse" => {
            let Yaml::String(text) = value else { return Err(Fault::expected(0, "string", value)); };
            let graph = Graph::parse_data(text).map_err(|error| Fault::at(0, "parse", format!("{} (YAML line {}, column {})", error.message, error.span.line, error.span.col)))?;
            Ok(data(&graph, ROOT))
        }
        "yaml_dump" => Ok(Yaml::String(emit::document(value))),
        _ => unreachable!("YAML conversion actor"),
    }
}

/// Read syntax without resolving symbols, evaluating lists, or linking imports.
fn data(graph: &Graph, id: NodeId) -> Yaml {
    match graph.kind(id) {
        Kind::Scalar { value, .. } => value.clone(),
        Kind::Seq(items) => Yaml::Array(items.iter().map(|&item| data(graph, item)).collect()),
        Kind::Map(entries) => {
            let mut result = Hash::new();
            for (key, value) in entries {
                result.insert(Yaml::String(key.clone()), data(graph, *value));
            }
            Yaml::Hash(result)
        }
    }
}
