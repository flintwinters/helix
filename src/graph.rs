//! The object graph: YAML parsed into an arena of nodes with parent links and source spans.
//! Parent links define lexical/object resolution; paths are always root-relative.

use yaml_rust2::parser::{Event, MarkedEventReceiver, Parser};
use yaml_rust2::scanner::{Marker, TScalarStyle};
use yaml_rust2::Yaml;

use crate::error::HelixError;
use crate::span::Span;

pub type NodeId = usize;

#[derive(Debug)]
pub enum Kind {
    /// `plain` is true for unquoted strings, which are symbols when they appear as expression arguments.
    Scalar { value: Yaml, plain: bool },
    Seq(Vec<NodeId>),
    Map(Vec<(String, NodeId)>),
}

#[derive(Debug)]
struct Node {
    kind: Kind,
    parent: Option<NodeId>,
    span: Span,
}

#[derive(Debug)]
pub struct Graph {
    nodes: Vec<Node>,
}

pub const ROOT: NodeId = 0;

impl Graph {
    pub fn parse(src: &str) -> Result<Graph, HelixError> {
        let mut builder = Builder::default();
        Parser::new_from_str(src)
            .load(&mut builder, false)
            .map_err(|e| HelixError {
                kind: "parse",
                message: e.to_string(),
                path: None,
                span: span(e.marker()),
            })?;
        if let Some(err) = builder.error {
            return Err(err);
        }
        if builder.graph.nodes.is_empty() {
            return Err(HelixError {
                kind: "parse",
                message: "empty document".into(),
                path: None,
                span: Span { line: 1, col: 1 },
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

impl Default for Graph {
    fn default() -> Self {
        Graph { nodes: Vec::new() }
    }
}

impl Builder {
    fn fail(&mut self, kind: &'static str, message: &str, at: Marker) {
        if self.error.is_none() {
            self.error = Some(HelixError { kind, message: message.into(), path: None, span: span(&at) });
        }
    }

    /// Attach a new node to the open container (or make it the root) and return its id.
    fn add(&mut self, kind: Kind, at: Marker) -> Option<NodeId> {
        let id = self.graph.nodes.len();
        let parent = self.stack.last().map(|(p, _)| *p);
        if let Some(parent) = parent {
            match &mut self.graph.nodes[parent].kind {
                Kind::Seq(items) => items.push(id),
                Kind::Map(entries) => {
                    let key = self.stack.last_mut().and_then(|(_, k)| k.take())?;
                    entries.push((key, id));
                }
                Kind::Scalar { .. } => unreachable!("scalars never open"),
            }
        }
        self.graph.nodes.push(Node { kind, parent, span: span(&at) });
        Some(id)
    }

    fn pending_key(&self) -> Option<bool> {
        let (id, key) = self.stack.last()?;
        matches!(self.graph.nodes[*id].kind, Kind::Map(_)).then(|| key.is_none())
    }
}

impl MarkedEventReceiver for Builder {
    fn on_event(&mut self, ev: Event, at: Marker) {
        if self.error.is_some() {
            return;
        }
        match ev {
            Event::Scalar(text, style, _, _) => {
                if self.pending_key() == Some(true) {
                    self.stack.last_mut().unwrap().1 = Some(text);
                    return;
                }
                let plain = style == TScalarStyle::Plain;
                let value = if plain { infer(&text) } else { Yaml::String(text) };
                let plain = plain && matches!(value, Yaml::String(_));
                self.add(Kind::Scalar { value, plain }, at);
            }
            Event::SequenceStart(..) | Event::MappingStart(..) => {
                if self.pending_key() == Some(true) {
                    return self.fail("unsupported", "mapping keys must be scalars", at);
                }
                let kind = if matches!(ev, Event::SequenceStart(..)) { Kind::Seq(vec![]) } else { Kind::Map(vec![]) };
                if let Some(id) = self.add(kind, at) {
                    self.stack.push((id, None));
                }
            }
            Event::SequenceEnd | Event::MappingEnd => {
                self.stack.pop();
            }
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
