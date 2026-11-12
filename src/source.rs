//! The boundary between parsed source and persisted executable values: capture,
//! recovery serialization, and cached reloading into the evaluator's graph.
//!
//! Ordinary values keep literal strings. Source scalars instead retain their quoting
//! through the emitter's verbatim `Yaml::Real` carrier, including quoted strings in
//! argument positions. Do not apply this conversion to already evaluated data.

use std::collections::HashMap;

use yaml_rust2::{yaml::Hash, Yaml};

use crate::emit;
use crate::error::HelixError;
use crate::graph::{Graph, Kind, NodeId, ROOT};

/// Parsing persisted source and adding it to a graph have different blame policies.
pub(crate) enum LoadError {
    Parse(HelixError),
    Graft(HelixError),
}

impl LoadError {
    pub(crate) fn error(self) -> HelixError {
        match self { Self::Parse(error) | Self::Graft(error) => error }
    }
}

/// Persisted callable sources already grafted into one evaluator's graph.
#[derive(Default)]
pub(crate) struct Grafts {
    roots: HashMap<String, NodeId>,
}

impl Grafts {
    /// Reconstitute each distinct source once; reuse the same lexical root on later calls.
    pub(crate) fn load(&mut self, graph: &mut Graph, value: &Yaml) -> Result<NodeId, LoadError> {
        let text = emit::document(value);
        if let Some(&root) = self.roots.get(&text) { return Ok(root); }
        let parsed = Graph::parse(&text).map_err(LoadError::Parse)?;
        let root = graph.graft(parsed).map_err(LoadError::Graft)?;
        self.roots.insert(text, root);
        Ok(root)
    }

    pub(crate) fn is_root(&self, id: NodeId) -> bool {
        self.roots.values().any(|&root| root == id)
    }
}

/// Completed root fields are data; unfinished fields retain their complete source.
/// Cached subexpressions must not replace source inside calls, where strings are symbols.
pub(crate) fn recovery(graph: &Graph, completed: &HashMap<NodeId, Yaml>) -> String {
    let value = match graph.kind(ROOT) {
        Kind::Map(entries) => Yaml::Hash(entries.iter().map(|(name, id)| {
            (Yaml::String(name.clone()), completed.get(id).cloned().unwrap_or_else(|| subtree(graph, *id)))
        }).collect()),
        _ => completed.get(&ROOT).cloned().unwrap_or_else(|| subtree(graph, ROOT)),
    };
    emit::document(&value)
}

/// Preserve a parsed subtree as executable source, without evaluating any node.
pub(crate) fn subtree(graph: &Graph, id: NodeId) -> Yaml {
    match graph.kind(id) {
        Kind::Scalar { value: Yaml::String(text), plain } => Yaml::Real(if *plain { text.clone() } else { emit::quoted(text) }),
        Kind::Scalar { value, .. } => value.clone(),
        Kind::Seq(items) => Yaml::Array(items.iter().map(|&child| subtree(graph, child)).collect()),
        Kind::Map(entries) => Yaml::Hash(entries.iter().map(|(key, child)| (Yaml::String(key.to_string()), subtree(graph, *child))).collect()),
    }
}

/// Capture literal binding values around function source; an empty capture needs no wrapper.
pub(crate) fn closure(function: Yaml, bindings: Hash) -> Yaml {
    if bindings.is_empty() { return function; }
    Yaml::Array(vec![Yaml::Real("closure".into()), Yaml::Hash(bindings), function])
}
