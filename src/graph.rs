//! The object graph: YAML parsed into an arena of nodes with parent links and source spans.
//! Parent links define lexical/object resolution; paths are always root-relative.

use std::collections::HashMap;

use yaml_rust2::parser::{Event, MarkedEventReceiver, Parser};
use yaml_rust2::scanner::{Marker, TScalarStyle};
use yaml_rust2::Yaml;

use crate::error::HelixError;
use crate::span::Span;

pub type NodeId = usize;

pub enum Kind {
    /// `plain` is true for unquoted strings, which are symbols when they appear as expression arguments.
    Scalar { value: Yaml, plain: bool },
    Seq(Vec<NodeId>),
    Map(Vec<(String, NodeId)>),
}

struct Node {
    kind: Kind,
    parent: Option<NodeId>,
    span: Span,
}

#[derive(Default)]
pub struct Graph {
    nodes: Vec<Node>,
    /// Imported files by the root of the subtree they were grafted as.
    sources: HashMap<NodeId, String>,
}

pub const ROOT: NodeId = 0;

impl Graph {
    pub fn parse(src: &str) -> Result<Graph, HelixError> {
        Self::parse_document(src, false)
    }

    /// Parse exactly one data document, checking the entire input stream.
    pub(crate) fn parse_data(src: &str) -> Result<Graph, HelixError> {
        Self::parse_document(src, true)
    }

    fn parse_document(src: &str, entire_stream: bool) -> Result<Graph, HelixError> {
        let mut builder = Builder::default();
        Parser::new_from_str(src)
            .load(&mut builder, entire_stream)
            .map_err(|e| HelixError {
                kind: "parse".into(),
                message: e.to_string(),
                file: None,
                path: None,
                span: span(e.marker()),
                phase: None, data: None, calls: Vec::new(), cause: None,
            })?;
        if let Some(err) = builder.error {
            return Err(err);
        }
        if builder.graph.nodes.is_empty() {
            return Err(HelixError {
                kind: "parse".into(),
                message: "empty document".into(),
                file: None,
                path: None,
                span: Span { line: 1, col: 1 },
                phase: None, data: None, calls: Vec::new(), cause: None,
            });
        }
        Ok(builder.graph)
    }

    pub fn kind(&self, id: NodeId) -> &Kind {
        &self.nodes[id].kind
    }

    pub fn span(&self, id: NodeId) -> Span {
        self.nodes[id].span
    }

    /// Root-relative path such as `/` or `/inner/y/1`.
    pub fn path(&self, id: NodeId) -> String {
        let mut parts = Vec::new();
        let mut cur = id;
        while let Some(parent) = self.nodes[cur].parent {
            parts.push(match &self.nodes[parent].kind {
                Kind::Map(entries) => entries.iter().find(|(_, c)| *c == cur).map(|(k, _)| k.clone()),
                Kind::Seq(items) => items.iter().position(|c| *c == cur).map(|i| i.to_string()),
                Kind::Scalar { .. } => None,
            }
            .expect("child is listed in its parent"));
            cur = parent;
        }
        parts.reverse();
        format!("/{}", parts.join("/"))
    }

    /// Append another graph as a detached subtree (its root has no parent) and return its root id.
    /// Used for code that arrives as data, such as a refinement's predicate.
    pub fn graft(&mut self, other: Graph) -> NodeId {
        let offset = self.nodes.len();
        let shift = |ids: &[NodeId]| ids.iter().map(|id| id + offset).collect::<Vec<_>>();
        for node in other.nodes {
            let kind = match node.kind {
                Kind::Seq(items) => Kind::Seq(shift(&items)),
                Kind::Map(entries) => Kind::Map(entries.into_iter().map(|(k, id)| (k, id + offset)).collect()),
                scalar => scalar,
            };
            self.nodes.push(Node { kind, parent: node.parent.map(|p| p + offset), span: node.span });
        }
        offset
    }

    /// Record that the subtree grafted at `root` is the file `name`; its errors report it.
    pub fn set_source(&mut self, root: NodeId, name: String) {
        self.sources.insert(root, name);
    }

    /// The imported file `id` belongs to, if it is not part of the main program.
    pub fn source(&self, id: NodeId) -> Option<&str> {
        let mut top = id;
        while let Some(parent) = self.nodes[top].parent {
            top = parent;
        }
        self.sources.get(&top).map(String::as_str)
    }

    /// An error of `kind` blamed on node `id`, with its path, span and file.
    pub fn error(&self, id: NodeId, kind: impl Into<String>, message: String) -> HelixError {
        HelixError { kind: kind.into(), message, file: self.source(id).map(str::to_string), path: Some(self.path(id)), span: self.span(id), phase: None, data: None, calls: Vec::new(), cause: None }
    }

    /// A plain (unquoted) string, which is a symbol in expression position.
    pub fn symbol(&self, id: NodeId) -> Option<&str> {
        match self.kind(id) {
            Kind::Scalar { value: Yaml::String(name), plain: true } => Some(name),
            _ => None,
        }
    }

    /// The symbol heading a sequence (quoted heads are data).
    pub fn form(&self, id: NodeId) -> Option<&str> {
        let Kind::Seq(items) = self.kind(id) else { return None };
        self.symbol(*items.first()?)
    }

    /// The enclosing container; parent links are what lexical resolution walks.
    pub fn parent(&self, id: NodeId) -> Option<NodeId> {
        self.nodes[id].parent
    }
}

/// yaml-rust2 markers have 1-indexed lines but 0-indexed columns; spans are 1-indexed on both axes.
fn span(m: &Marker) -> Span {
    Span { line: m.line(), col: m.col() + 1 }
}

#[derive(Default)]
struct Builder {
    graph: Graph,
    /// Open containers; for maps, `key` holds the pending key awaiting its value.
    stack: Vec<(NodeId, Option<String>)>,
    error: Option<HelixError>,
}

impl Builder {
    /// Record the first error; `on_event` ignores everything after it.
    fn fail(&mut self, kind: &'static str, message: &str, at: Marker) {
        self.error = Some(HelixError { kind: kind.into(), message: message.into(), file: None, path: None, span: span(&at), phase: None, data: None, calls: Vec::new(), cause: None });
    }

    /// Attach a new node to the open container (or make it the root) and return its id.
    fn add(&mut self, kind: Kind, at: Marker) -> NodeId {
        let id = self.graph.nodes.len();
        let parent = self.stack.last().map(|(p, _)| *p);
        if let Some(parent) = parent {
            match &mut self.graph.nodes[parent].kind {
                Kind::Seq(items) => items.push(id),
                Kind::Map(entries) => {
                    let key = self.stack.last_mut().and_then(|(_, k)| k.take()).expect("a map value follows its key");
                    entries.push((key, id));
                }
                Kind::Scalar { .. } => unreachable!("scalars never open"),
            }
        }
        self.graph.nodes.push(Node { kind, parent, span: span(&at) });
        id
    }

    /// Whether the open container is a map whose next scalar is a key.
    fn awaiting_key(&self) -> bool {
        self.stack.last().is_some_and(|(id, key)| matches!(self.graph.nodes[*id].kind, Kind::Map(_)) && key.is_none())
    }
}

impl MarkedEventReceiver for Builder {
    fn on_event(&mut self, ev: Event, at: Marker) {
        if self.error.is_some() {
            return;
        }
        match ev {
            Event::Scalar(text, style, _, _) => {
                if self.awaiting_key() {
                    let id = self.stack.last().unwrap().0;
                    if matches!(self.graph.kind(id), Kind::Map(entries) if entries.iter().any(|(key, _)| key == &text)) {
                        return self.fail("parse", "duplicate mapping key", at);
                    }
                    self.stack.last_mut().unwrap().1 = Some(text);
                    return;
                }
                let plain = style == TScalarStyle::Plain;
                let value = if plain { infer(&text) } else { Yaml::String(text) };
                let plain = plain && matches!(value, Yaml::String(_));
                self.add(Kind::Scalar { value, plain }, at);
            }
            Event::SequenceStart(..) | Event::MappingStart(..) => {
                if self.awaiting_key() {
                    return self.fail("unsupported", "mapping keys must be scalars", at);
                }
                let kind = if matches!(ev, Event::SequenceStart(..)) { Kind::Seq(vec![]) } else { Kind::Map(vec![]) };
                let id = self.add(kind, at);
                self.stack.push((id, None));
            }
            Event::SequenceEnd | Event::MappingEnd => {
                self.stack.pop();
            }
            Event::DocumentStart if !self.graph.nodes.is_empty() => self.fail("parse", "expected exactly one YAML document", at),
            Event::Alias(_) => self.fail("unsupported", "aliases are not supported", at),
            _ => {}
        }
    }
}

/// YAML 1.2 core-schema inference for plain scalars (floats are kept verbatim as `Real`).
fn infer(text: &str) -> Yaml {
    match text {
        "" | "~" | "null" | "Null" | "NULL" => Yaml::Null,
        "true" | "True" | "TRUE" => Yaml::Boolean(true),
        "false" | "False" | "FALSE" => Yaml::Boolean(false),
        _ => {
            if let Ok(i) = text.parse::<i64>() {
                Yaml::Integer(i)
            } else if text.parse::<f64>().is_ok_and(f64::is_finite) && text.bytes().any(|b| b.is_ascii_digit()) {
                Yaml::Real(text.to_string())
            } else {
                Yaml::String(text.to_string())
            }
        }
    }
}
