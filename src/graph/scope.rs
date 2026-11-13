//! Lexical scope over source: function parameter declarations and the declaration a name denotes.
//! Both depend on the graph alone, so execution and non-executing analysis share them.
use super::{Graph, Kind, NodeId, Syntax};
use crate::model::HelixError;

/// Where a lexical name is declared.
pub(crate) enum Site {
    /// A map entry or `let`/`closure` binding.
    Entry(NodeId),
    /// The `index`th parameter of the `fn` at the node.
    Parameter(NodeId, usize),
}

impl Graph {
    /// A `fn`'s parameter names with their annotation nodes, in order.
    pub(crate) fn parameters(&self, function: NodeId) -> Result<Vec<(String, Option<NodeId>)>, HelixError> {
        let Some(items) = self.call(function) else {
            unreachable!()
        };
        if !matches!(items.len(), 3 | 4) {
            return Err(self.error(
                function,
                "arity",
                format!("`fn` takes 2 to 3 arguments, got {}", items.len().saturating_sub(1)),
            ));
        }
        let malformed = || {
            self.error(
                items[1],
                "type",
                "parameters must be a list of names or `[name, type]` pairs".into(),
            )
        };
        let Kind::Seq(params) = self.kind(items[1]) else {
            return Err(malformed());
        };
        params
            .iter()
            .map(|&p| match self.kind(p) {
                Kind::Seq(pair) if pair.len() == 2 => Ok((
                    self.identifier(pair[0]).ok_or_else(malformed)?.into(),
                    Some(pair[1]),
                )),
                _ => Ok((self.identifier(p).ok_or_else(malformed)?.into(), None)),
            })
            .collect()
    }

    /// The innermost lexical declaration of `name` visible from `from`, independent of any frame.
    pub(crate) fn lexical_site(&self, from: NodeId, name: &str) -> Option<Site> {
        let entry = |entries: &[(String, NodeId)]| {
            entries.iter().find(|(k, _)| k == name).map(|(_, id)| Site::Entry(*id))
        };
        let mut child = from;
        while let Some(id) = self.lexical_parent(child) {
            let found = match self.syntax(id) {
                Syntax::Mapping(entries) => entry(entries),
                Syntax::Call(items) if items.get(2) == Some(&child) => match (self.form(id), self.kind(items[1])) {
                    (Some("fn"), _) => self
                        .parameters(id)
                        .ok()
                        .and_then(|p| p.iter().position(|(n, _)| n == name))
                        .map(|index| Site::Parameter(id, index)),
                    (Some("let" | "closure"), Kind::Map(entries)) => entry(entries),
                    _ => None,
                },
                _ => None,
            };
            if found.is_some() {
                return found;
            }
            child = id;
        }
        None
    }

    /// The declaration node a possibly dotted lexical name denotes, following keys through maps;
    /// `module` maps an import to the root of the module it links, the identity when nothing links.
    /// `None` for parameters and unresolved names.
    pub(crate) fn named_node(&self, from: NodeId, name: &str, module: &dyn Fn(NodeId) -> NodeId) -> Option<NodeId> {
        let (head, rest) = match name.split_once('.') {
            Some((head, rest)) => (head, Some(rest)),
            None => (name, None),
        };
        let Some(Site::Entry(mut node)) = self.lexical_site(from, head) else {
            return None;
        };
        for part in rest.into_iter().flat_map(|rest| rest.split('.')) {
            let Syntax::Mapping(entries) = self.syntax(module(node)) else {
                return None;
            };
            node = entries.iter().find(|(key, _)| key == part)?.1;
        }
        Some(node)
    }
}
