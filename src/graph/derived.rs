//! Memoized facts that are pure functions of the immutable source graph.
//!
//! The YAML graph stays the only authority: every entry is computed by the same graph
//! functions a non-caching reader would call (`lexical_site`, `parameters`, ...), at most
//! once per node, and is dropped whenever the graph is mutated. Nothing here is
//! serialized, inspected or compared, so deleting the cache changes no behavior.
use super::scope::Site;
use super::{Graph, NodeId};
use crate::model::HelixError;
use std::sync::OnceLock;

pub(crate) type Parameters = Vec<(String, Option<NodeId>)>;

/// Lexical declarations a name written at one node resolves to. A name is a function of
/// its node (a reference's text, or a call's actor), so the cache records the name it was
/// computed for and a lookup with any other name bypasses it.
#[derive(Clone)]
pub(crate) struct Sites {
    name: String,
    pub(crate) exact: Option<Site>,
    /// The declaration of the root of a dotted name.
    pub(crate) root: Option<Site>,
}

pub(crate) struct Tables {
    sites: Vec<OnceLock<Sites>>,
    parameters: Vec<OnceLock<Result<Parameters, HelixError>>>,
    /// Enclosing `fn` nodes: the frames a closure created here captures.
    ancestors: Vec<OnceLock<Box<[NodeId]>>>,
}

/// Lazily built per graph; reset by every mutation. Clones start empty; equality and debug
/// output ignore it.
#[derive(Default)]
pub(crate) struct Derived(OnceLock<Tables>);

impl Derived {
    pub(crate) fn reset(&mut self) {
        self.0.take();
    }
}
impl Clone for Derived {
    fn clone(&self) -> Self {
        Self::default()
    }
}
impl PartialEq for Derived {
    fn eq(&self, _: &Self) -> bool {
        true
    }
}
impl std::fmt::Debug for Derived {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("Derived")
    }
}

fn cells<T>(len: usize) -> Vec<OnceLock<T>> {
    (0..len).map(|_| OnceLock::new()).collect()
}

impl Graph {
    fn tables(&self) -> &Tables {
        self.derived.0.get_or_init(|| Tables {
            sites: cells(self.nodes.len()),
            parameters: cells(self.nodes.len()),
            ancestors: cells(self.nodes.len()),
        })
    }

    /// The declarations `name`, written at `from`, resolves to.
    pub(crate) fn sites(&self, from: NodeId, name: &str) -> std::borrow::Cow<'_, Sites> {
        let cell = &self.tables().sites[from];
        let found = cell.get_or_init(|| self.compute_sites(from, name));
        if found.name == name {
            return std::borrow::Cow::Borrowed(found);
        }
        std::borrow::Cow::Owned(self.compute_sites(from, name))
    }

    fn compute_sites(&self, from: NodeId, name: &str) -> Sites {
        Sites {
            name: name.to_string(),
            exact: self.lexical_site_uncached(from, name),
            root: name.split_once('.').and_then(|(head, _)| self.root_site(from, head)),
        }
    }

    /// Parameters of the `fn` at `function`, parsed once.
    pub(crate) fn params(&self, function: NodeId) -> Result<&Parameters, &HelixError> {
        self.tables().parameters[function].get_or_init(|| self.parameters_uncached(function)).as_ref()
    }

    /// The `fn` nodes enclosing `node`, innermost first.
    pub(crate) fn enclosing_functions(&self, node: NodeId) -> &[NodeId] {
        self.tables().ancestors[node].get_or_init(|| {
            let mut found = Vec::new();
            let mut parent = self.parent(node);
            while let Some(id) = parent {
                if self.form(id) == Some("fn") {
                    found.push(id);
                }
                parent = self.parent(id);
            }
            found.into()
        })
    }
}
