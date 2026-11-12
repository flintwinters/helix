//! The object graph: YAML parsed into an arena of nodes with parent links and source spans.
//! Parent links define lexical/object resolution; paths are always root-relative.

use yaml_rust2::Yaml;
use yaml_rust2::parser::{Event, MarkedEventReceiver, Parser};
use yaml_rust2::scanner::{Marker, TScalarStyle};

use crate::error::HelixError;
use crate::model::{Node, Span};

pub type NodeId = usize;

pub(crate) use crate::model::{Graph, Kind, ROOT, MAX_NODES, MAX_NESTING};

impl Graph {
    pub fn parse(src: &str) -> Result<Graph, HelixError> {
        Self::parse_document(src, false)
    }

    /// Parse exactly one data document, checking the entire input stream.
    pub(crate) fn parse_data(src: &str) -> Result<Graph, HelixError> {
        Self::parse_document(src, true)
    }

    fn parse_document(src: &str, data_only: bool) -> Result<Graph, HelixError> {
        let source_lines = src
            .split_inclusive('\n')
            .flat_map(|line| {
                let line = line.strip_suffix('\n').unwrap_or(line);
                line.strip_suffix('\r').unwrap_or(line).split('\r')
            })
            .collect();
        let mut builder = Builder {
            source_lines,
            ends_with_break: src.ends_with(['\r', '\n']),
            reject_tags: data_only,
            ..Builder::default()
        };
        let parsed = Parser::new_from_str(src)
            .load(&mut builder, data_only)
            .map_err(|e| HelixError {
                kind: "parse".into(),
                message: e.to_string(),
                file: None,
                path: None,
                reference: None,
                span: span(e.marker()),
                phase: None,
                data: None,
                calls: Vec::new(),
                cause: None,
            });
        if let Some(err) = builder.error {
            return Err(err);
        }
        parsed?;
        if builder.graph.nodes.is_empty() {
            return Err(HelixError {
                kind: "parse".into(),
                message: "empty document".into(),
                file: None,
                path: None,
                reference: None,
                span: Span { line: 1, col: 1 },
                phase: None,
                data: None,
                calls: Vec::new(),
                cause: None,
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
            parts.push(
                match &self.nodes[parent].kind {
                    Kind::Map(entries) => entries
                        .iter()
                        .find(|(_, c)| *c == cur)
                        .map(|(k, _)| k.clone()),
                    Kind::Seq(items) => items.iter().position(|c| *c == cur).map(|i| i.to_string()),
                    Kind::Scalar { .. } => None,
                }
                .expect("child is listed in its parent"),
            );
            cur = parent;
        }
        parts.reverse();
        format!("/{}", parts.join("/"))
    }

    /// String keys and integer indices are distinct even for empty, numeric or slash keys.
    pub fn segments(&self, id: NodeId) -> Vec<crate::model::Segment> {
        use crate::model::Segment;
        let mut parts = Vec::new();
        let mut cur = id;
        while let Some(parent) = self.parent(cur) {
            parts.push(match self.kind(parent) {
                Kind::Map(entries) => {
                    Segment::Key(entries.iter().find(|(_, c)| *c == cur).unwrap().0.clone())
                }
                Kind::Seq(items) => Segment::Index(items.iter().position(|c| *c == cur).unwrap()),
                _ => unreachable!(),
            });
            cur = parent;
        }
        parts.reverse();
        parts
    }
    pub fn reference(&self, id: NodeId) -> crate::value::Value {
        use crate::model::{Segment, string as s};
        use crate::value::Value as V;
        V::Hash(
            [
                (s("source"), s(self.source(id).unwrap_or("<root>"))),
                (
                    s("path"),
                    V::Array(
                        self.segments(id)
                            .into_iter()
                            .map(|segment| match segment {
                                Segment::Key(key) => V::String(key),
                                Segment::Index(index) => V::Integer(index as i64),
                            })
                            .collect(),
                    ),
                ),
            ]
            .into_iter()
            .collect(),
        )
    }

    /// Shared code views are built once, bottom-up, over the immutable linked source arena.
    pub fn code_views(&self) -> Vec<crate::value::Value> {
        use crate::value::Value as V;
        let mut code = vec![V::Null; self.nodes.len()];
        for id in (0..self.nodes.len()).rev() {
            code[id] = match self.kind(id) {
                Kind::Scalar {
                    value: Yaml::String(text),
                    plain: true,
                } => V::Hash(
                    [(V::String("symbol".into()), V::String(text.clone()))]
                        .into_iter()
                        .collect(),
                ),
                Kind::Scalar { value, .. } => V::from_yaml(value),
                Kind::Seq(items) => V::Array(items.iter().map(|&c| code[c].clone()).collect()),
                Kind::Map(entries) => V::Hash(
                    entries
                        .iter()
                        .map(|(k, c)| (V::String(k.clone()), code[*c].clone()))
                        .collect(),
                ),
            };
        }
        code
    }

    /// Structural code equality uses authoritative syntax, never an inspection projection.
    /// Container order and scalar kinds/quoting matter because code controls evaluation order.
    pub fn same_code(&self, left: NodeId, other: &Graph, right: NodeId) -> bool {
        let mut pending = vec![(left, right)];
        while let Some((a, b)) = pending.pop() {
            match (self.kind(a), other.kind(b)) {
                (
                    Kind::Scalar {
                        value: a,
                        plain: ap,
                    },
                    Kind::Scalar {
                        value: b,
                        plain: bp,
                    },
                ) if a == b && ap == bp => {}
                (Kind::Seq(a), Kind::Seq(b)) if a.len() == b.len() => {
                    pending.extend(a.iter().copied().zip(b.iter().copied()))
                }
                (Kind::Map(a), Kind::Map(b)) if a.len() == b.len() => {
                    for ((key, a), (other, b)) in a.iter().zip(b) {
                        if key != other {
                            return false;
                        }
                        pending.push((*a, *b));
                    }
                }
                _ => return false,
            }
        }
        true
    }

    /// Append another graph as a detached subtree (its root has no parent) and return its root id.
    /// Used for code that arrives as data, such as a refinement's predicate.
    pub fn graft(&mut self, other: Graph) -> Result<NodeId, HelixError> {
        if self.nodes.len() + other.nodes.len() > MAX_NODES {
            return Err(self.error(ROOT, "limit", format!("graph exceeds {MAX_NODES} nodes")));
        }
        let offset = self.nodes.len();
        let shift = |ids: &[NodeId]| ids.iter().map(|id| id + offset).collect::<Vec<_>>();
        for node in other.nodes {
            let kind = match node.kind {
                Kind::Seq(items) => Kind::Seq(shift(&items)),
                Kind::Map(entries) => Kind::Map(
                    entries
                        .into_iter()
                        .map(|(k, id)| (k, id + offset))
                        .collect(),
                ),
                scalar => scalar,
            };
            self.nodes.push(Node {
                kind,
                parent: node.parent.map(|p| p + offset),
                span: node.span,
            });
        }
        Ok(offset)
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
        HelixError {
            kind: kind.into(),
            message,
            file: self.source(id).map(str::to_string),
            path: Some(self.path(id)),
            reference: Some(self.segments(id)),
            span: self.span(id),
            phase: None,
            data: None,
            calls: Vec::new(),
            cause: None,
        }
    }

    /// A plain (unquoted) string, which is a symbol in expression position.
    pub fn symbol(&self, id: NodeId) -> Option<&str> {
        match self.kind(id) {
            Kind::Scalar {
                value: Yaml::String(name),
                plain: true,
            } => Some(name),
            _ => None,
        }
    }

    /// The symbol heading a sequence (quoted heads are data).
    pub fn form(&self, id: NodeId) -> Option<&str> {
        let Kind::Seq(items) = self.kind(id) else {
            return None;
        };
        self.symbol(*items.first()?)
    }

    /// The enclosing container; parent links are what lexical resolution walks.
    pub fn parent(&self, id: NodeId) -> Option<NodeId> {
        self.nodes[id].parent
    }
}

/// yaml-rust2 markers have 1-indexed lines but 0-indexed columns; spans are 1-indexed on both axes.
fn span(m: &Marker) -> Span {
    Span {
        line: m.line(),
        col: m.col() + 1,
    }
}

#[derive(Default)]
struct Builder<'src> {
    graph: Graph,
    source_lines: Vec<&'src str>,
    ends_with_break: bool,
    /// Data conversion cannot preserve or interpret explicit YAML tags.
    reject_tags: bool,
    /// Open containers; for maps, `key` holds the pending key awaiting its value.
    stack: Vec<(NodeId, Option<String>)>,
    error: Option<HelixError>,
}

impl Builder<'_> {
    /// Record the first error; `on_event` ignores everything after it.
    fn fail(&mut self, kind: &'static str, message: &str, at: Marker) {
        self.error = Some(HelixError {
            kind: kind.into(),
            message: message.into(),
            file: None,
            path: None,
            reference: None,
            span: span(&at),
            phase: None,
            data: None,
            calls: Vec::new(),
            cause: None,
        });
    }

    /// Correct yaml-rust2 0.13 empty-block chomping: clip/strip discard all
    /// breaks; keep retains content-line breaks, never the header break.
    fn block_scalar(&self, mut text: String, style: TScalarStyle, at: Marker) -> String {
        if matches!(style, TScalarStyle::Literal | TScalarStyle::Folded)
            && text.chars().all(|ch| ch == '\n')
        {
            // At EOF the scanner marks the header; otherwise it marks the next
            // content/token line. For an empty block, the preceding nonblank line is its header.
            let marked = at.line() - 1;
            let indicator = if style == TScalarStyle::Literal {
                '|'
            } else {
                '>'
            };
            let starts_here = self.source_lines[marked].chars().nth(at.col()) == Some(indicator);
            let header_line = if starts_here {
                marked
            } else {
                (0..marked)
                    .rev()
                    .find(|&line| !self.source_lines[line].trim().is_empty())
                    .unwrap_or(marked)
            };
            let keep = block_keep(self.source_lines[header_line], indicator);
            let content_line = header_line + 1;
            let blank_lines = self
                .source_lines
                .iter()
                .skip(content_line)
                .take_while(|line| line.chars().all(|ch| ch == ' ' || ch == '\t'))
                .count();
            let unterminated = blank_lines > 0
                && content_line + blank_lines == self.source_lines.len()
                && !self.ends_with_break;
            text = if keep {
                "\n".repeat(blank_lines - usize::from(unterminated))
            } else {
                String::new()
            };
        }
        text
    }

    /// Attach a new node to the open container (or make it the root) and return its id.
    fn add(&mut self, kind: Kind, at: Marker) -> NodeId {
        let id = self.graph.nodes.len();
        let parent = self.stack.last().map(|(p, _)| *p);
        if let Some(parent) = parent {
            match &mut self.graph.nodes[parent].kind {
                Kind::Seq(items) => items.push(id),
                Kind::Map(entries) => {
                    let key = self
                        .stack
                        .last_mut()
                        .and_then(|(_, k)| k.take())
                        .expect("a map value follows its key");
                    entries.push((key, id));
                }
                Kind::Scalar { .. } => unreachable!("scalars never open"),
            }
        }
        self.graph.nodes.push(Node {
            kind,
            parent,
            span: span(&at),
        });
        id
    }

    /// Whether the open container is a map whose next scalar is a key.
    fn awaiting_key(&self) -> bool {
        self.stack.last().is_some_and(|(id, key)| {
            matches!(self.graph.nodes[*id].kind, Kind::Map(_)) && key.is_none()
        })
    }
}

impl MarkedEventReceiver for Builder<'_> {
    fn on_event(&mut self, ev: Event, at: Marker) {
        if self.error.is_some() {
            return;
        }
        if self.reject_tags
            && matches!(
                &ev,
                Event::Scalar(_, _, _, Some(_))
                    | Event::SequenceStart(_, Some(_))
                    | Event::MappingStart(_, Some(_))
            )
        {
            return self.fail("unsupported", "YAML tags are not supported", at);
        }
        if self.graph.nodes.len() >= MAX_NODES
            && matches!(
                ev,
                Event::Scalar(..) | Event::SequenceStart(..) | Event::MappingStart(..)
            )
        {
            return self.fail("limit", &format!("graph exceeds {MAX_NODES} nodes"), at);
        }
        match ev {
            Event::Scalar(text, style, _, _) => {
                let text = self.block_scalar(text, style, at);
                if self.awaiting_key() {
                    let id = self.stack.last().unwrap().0;
                    if matches!(self.graph.kind(id), Kind::Map(entries) if entries.iter().any(|(key, _)| key == &text))
                    {
                        return self.fail("parse", "duplicate mapping key", at);
                    }
                    self.stack.last_mut().unwrap().1 = Some(text);
                    return;
                }
                let plain = style == TScalarStyle::Plain;
                let value = if plain {
                    infer(&text)
                } else {
                    Yaml::String(text)
                };
                let plain = plain && matches!(value, Yaml::String(_));
                self.add(Kind::Scalar { value, plain }, at);
            }
            Event::SequenceStart(..) | Event::MappingStart(..) => {
                if self.stack.len() >= MAX_NESTING {
                    return self.fail(
                        "depth",
                        &format!("syntax nested deeper than {MAX_NESTING} containers"),
                        at,
                    );
                }
                if self.awaiting_key() {
                    return self.fail("unsupported", "mapping keys must be scalars", at);
                }
                let kind = if matches!(ev, Event::SequenceStart(..)) {
                    Kind::Seq(vec![])
                } else {
                    Kind::Map(vec![])
                };
                let id = self.add(kind, at);
                self.stack.push((id, None));
            }
            Event::SequenceEnd | Event::MappingEnd => {
                self.stack.pop();
            }
            Event::DocumentStart if !self.graph.nodes.is_empty() => {
                self.fail("parse", "expected exactly one YAML document", at)
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
            } else if text.parse::<f64>().is_ok_and(f64::is_finite)
                && text.bytes().any(|b| b.is_ascii_digit())
            {
                Yaml::Real(text.to_string())
            } else {
                Yaml::String(text.to_string())
            }
        }
    }
}

/// Find the block header after its key/sequence prefix, including quoted keys and properties.
fn block_keep(header: &str, indicator: char) -> bool {
    let mut quote = None;
    let (mut plain, mut property, mut previous_space) = (false, false, true);
    let mut chars = header.chars().peekable();
    while let Some(ch) = chars.next() {
        if let Some(delimiter) = quote {
            if delimiter == '"' && ch == '\\' {
                chars.next();
            } else if ch == delimiter {
                if delimiter == '\'' && chars.peek() == Some(&'\'') {
                    chars.next();
                } else {
                    quote = None;
                }
            }
            continue;
        }
        if ch == '#' && previous_space {
            break;
        }
        if matches!(ch, ' ' | '\t') {
            if property {
                plain = false;
                property = false;
            }
            previous_space = true;
            continue;
        }
        previous_space = false;
        if ch == ':' && chars.peek().is_none_or(|next| matches!(next, ' ' | '\t')) {
            plain = false;
            continue;
        }
        if !plain {
            match ch {
                '\'' | '"' => {
                    quote = Some(ch);
                    continue;
                }
                '-' | '?' if chars.peek().is_some_and(|next| matches!(next, ' ' | '\t')) => {
                    continue;
                }
                '!' | '&' => {
                    property = true;
                }
                _ if ch == indicator => {
                    return chars
                        .take_while(|ch| matches!(ch, '+' | '-' | '1'..='9'))
                        .any(|ch| ch == '+');
                }
                _ => {}
            }
        }
        plain = true;
    }
    false
}
