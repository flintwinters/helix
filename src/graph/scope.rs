//! Lexical scope over source: function parameter declarations and the declaration a name denotes.
//! Both depend on the graph alone, so execution and non-executing analysis share them.
use super::{Graph, Kind, NodeId, Syntax};
use crate::model::HelixError;

/// Where a lexical name is declared.
pub(crate) enum Site {
    /// A map entry or `let` binding.
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
        let Kind::Seq(params) = self.kind(items[1]) else {
            return Err(self
                .error(items[1], "type", "`fn` parameters must be a list of names or `[name, type]` pairs".into())
                .with_hint(Some("write the parameters as a list, even for one: `!call [fn, [x], body]`".into())));
        };
        let malformed = |i: usize, p: NodeId| {
            self.error(p, "type", format!("`fn` parameter {} must be a name or a `[name, type]` pair", i + 1))
                .with_hint(Some("write `x` or `[x, integer]`; a default or extra entry belongs in the body".into()))
        };
        params
            .iter()
            .enumerate()
            .map(|(i, &p)| match self.kind(p) {
                Kind::Seq(pair) if pair.len() == 2 => Ok((
                    self.identifier(pair[0]).ok_or_else(|| malformed(i, p))?.into(),
                    Some(pair[1]),
                )),
                _ => Ok((self.identifier(p).ok_or_else(|| malformed(i, p))?.into(), None)),
            })
            .collect()
    }

    /// The innermost lexical declaration of the exact name `name` visible from `from`,
    /// independent of any frame.
    pub(crate) fn lexical_site(&self, from: NodeId, name: &str) -> Option<Site> {
        self.declarations(from, false, |declared, site| (declared == name).then_some(site))
    }

    /// The declaration of `head` as the root of a dotted name `head.member`. A member of the
    /// entry being defined is a sibling, not the entry itself, so `{fields: {a: !ref fields.b}}`
    /// sees its own entry.
    pub(crate) fn root_site(&self, from: NodeId, head: &str) -> Option<Site> {
        self.declarations(from, true, |declared, site| (declared == head).then_some(site))
    }

    /// The entry named `name` whose own value contains `from` outside any function body: the
    /// declaration a self-reference would have named had entries been visible to themselves.
    pub(crate) fn own_entry(&self, from: NodeId, name: &str) -> Option<NodeId> {
        match self.root_site(from, name)? {
            Site::Entry(node) if self.lexical_site(from, name).is_none_or(|site| !matches!(site, Site::Entry(n) if n == node)) => Some(node),
            _ => None,
        }
    }

    /// Every lexical name visible from `from`, innermost scope first.
    pub(crate) fn visible_names(&self, from: NodeId) -> Vec<String> {
        let mut names = Vec::new();
        self.declarations(from, false, |declared, _| {
            names.push(declared.to_string());
            None::<()>
        });
        names
    }

    /// Visit the declarations enclosing `from` innermost scope first, stopping at the first
    /// `Some` from `visit`. An entry whose own value contains `from` outside any function body
    /// is not visible unless `own` asks for it: reading it whole could only be a cycle, so
    /// `{name: !ref name}` names the enclosing `name`. Inside a function body the entry stays
    /// visible, which is recursion.
    fn declarations<T>(&self, from: NodeId, own: bool, mut visit: impl FnMut(&str, Site) -> Option<T>) -> Option<T> {
        let mut child = from;
        let mut deferred = false;
        while let Some(id) = self.lexical_parent(child) {
            let found = match self.syntax(id) {
                Syntax::Mapping(entries) => entries
                    .iter()
                    .filter(|(_, v)| own || deferred || *v != child)
                    .find_map(|(k, v)| visit(k, Site::Entry(*v))),
                Syntax::Call(items) if items.get(2) == Some(&child) => match (self.form(id), self.kind(items[1])) {
                    (Some("fn"), _) => self.parameters(id).ok().and_then(|params| {
                        params.iter().enumerate().find_map(|(index, (n, _))| visit(n, Site::Parameter(id, index)))
                    }),
                    (Some("let"), Kind::Map(entries)) => entries.iter().find_map(|(k, v)| visit(k, Site::Entry(*v))),
                    _ => None,
                },
                _ => None,
            };
            if found.is_some() {
                return found;
            }
            deferred |= self.form(id) == Some("fn") && self.call(id).and_then(|items| items.get(2)) == Some(&child);
            child = id;
        }
        None
    }

    /// Select the lexical base without evaluating member access. An exact name anywhere
    /// in the lexical chain wins before a dotted root, even a nearer root declaration.
    /// The suffix borrows the requested name and is absent for an exact binding.
    pub(crate) fn lexical_base<'n>(&self, from: NodeId, name: &'n str) -> Option<(Site, Option<&'n str>)> {
        if let Some(site) = self.lexical_site(from, name) {
            return Some((site, None));
        }
        let (head, rest) = name.split_once('.')?;
        self.root_site(from, head).map(|site| (site, Some(rest)))
    }

    /// The declaration node a possibly dotted lexical name denotes, following keys through maps;
    /// `module` maps an import to the root of the module it links, the identity when nothing links.
    /// `None` for parameters and unresolved names.
    pub(crate) fn named_node(&self, from: NodeId, name: &str, module: &dyn Fn(NodeId) -> NodeId) -> Option<NodeId> {
        let Some((Site::Entry(mut node), rest)) = self.lexical_base(from, name) else {
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
