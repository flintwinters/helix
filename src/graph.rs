//! The object graph: YAML parsed into an arena of nodes with parent links and source spans.
//! Parent links define lexical/object resolution; paths are always root-relative.

mod derived;
mod effects;
mod hint;
pub(crate) mod http;
mod scope;
mod syntax;
pub(crate) use derived::Derived;
pub(crate) use effects::Effects;
pub(crate) use hint::{members_of, missing_key, missing_member, nearest, not_callable};
pub(crate) use scope::Site;
pub(crate) use syntax::Syntax;

use yaml_rust2::Yaml;
use yaml_rust2::parser::{Event, MarkedEventReceiver, Parser};
use yaml_rust2::scanner::{Marker, TScalarStyle};

use crate::model::HelixError;
use crate::model::{Node, Span};

pub type NodeId = usize;

pub(crate) use crate::model::{Graph, Kind, MAX_NESTING, MAX_NODES, ROOT};

impl Graph {
    pub fn parse(src: &str) -> Result<Graph, HelixError> {
        Self::parse_document(src, false)
    }

    /// Parse exactly one data document, checking the entire input stream.
    pub(crate) fn parse_data(src: &str) -> Result<Graph, HelixError> {
        Self::parse_document(src, true)
    }

    fn parse_document(src: &str, _data_only: bool) -> Result<Graph, HelixError> {
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
            ..Builder::default()
        };
        let parsed = Parser::new_from_str(src)
            .load(&mut builder, true)
            .map_err(|e| {
                HelixError::unlocated("parse", e.info().to_owned(), span(e.marker()))
                    .with_hint(hint::yaml(src, e.info(), e.marker().index()))
            });
        if let Some(err) = builder.error {
            return Err(err);
        }
        parsed?;
        if builder.graph.nodes.is_empty() {
            return Err(HelixError::unlocated("parse", "empty document".into(), Span { line: 1, col: 1 }));
        }
        builder.graph.derived.reset();
        Ok(builder.graph)
    }

    pub fn kind(&self, id: NodeId) -> &Kind {
        &self.nodes[id].kind
    }

    pub fn span(&self, id: NodeId) -> Span {
        self.nodes[id].span
    }

    /// `file:line:column` (or `line:column` in the program itself) for naming a node in prose.
    pub fn place(&self, id: NodeId) -> String {
        let span = self.span(id);
        match self.source(id) {
            Some(file) => format!("{file}:{}:{}", span.line, span.col),
            None => format!("{}:{}", span.line, span.col),
        }
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
    /// The observable identity of a node's position; `reference` renders it.
    pub fn location(&self, id: NodeId) -> crate::model::Location {
        (self.source(id).unwrap_or("<root>").to_owned(), self.segments(id))
    }
    pub fn reference(&self, id: NodeId) -> crate::value::Value {
        render_location(&self.location(id))
    }

    /// Shared code views are built once, bottom-up, over the immutable linked source arena.
    pub fn code_views(&self) -> Vec<crate::value::Value> {
        use crate::value::Value as V;
        let mut code = vec![V::Null; self.nodes.len()];
        for id in (0..self.nodes.len()).rev() {
            code[id] = match self.kind(id) {
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

    /// Read syntax without resolving symbols, evaluating lists, or linking imports.
    pub(crate) fn data(&self, id: NodeId) -> crate::value::Value {
        use crate::value::{Hash, Value};
        match self.kind(id) {
            Kind::Scalar { value, .. } => Value::from_yaml(value),
            Kind::Seq(items) => Value::Array(items.iter().map(|&item| self.data(item)).collect()),
            Kind::Map(entries) => {
                let mut result = Hash::new();
                for (key, value) in entries {
                    result.insert(Value::String(key.clone()), self.data(*value));
                }
                Value::Hash(result)
            }
        }
    }

    /// Structural code equality uses authoritative syntax, never an inspection projection.
    /// Sequence order matters; scalar presentation and mapping order do not.
    pub fn same_code(&self, left: NodeId, other: &Graph, right: NodeId) -> bool {
        let mut pending = vec![(left, right)];
        while let Some((a, b)) = pending.pop() {
            match (self.kind(a), other.kind(b)) {
                (Kind::Scalar { value: a }, Kind::Scalar { value: b })
                    if a == b
                        || matches!((a, b), (Yaml::Real(a), Yaml::Real(b)) if a.parse::<f64>().ok() == b.parse::<f64>().ok()) =>
                    {}
                (Kind::Seq(a), Kind::Seq(b)) if a.len() == b.len() => {
                    pending.extend(a.iter().copied().zip(b.iter().copied()))
                }
                (Kind::Map(a), Kind::Map(b)) if a.len() == b.len() => {
                    for (key, a) in a {
                        let Some((_, b)) = b.iter().find(|(other, _)| other == key) else {
                            return false;
                        };
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
    pub fn graft(&mut self, mut other: Graph) -> Result<NodeId, HelixError> {
        if self.nodes.len() + other.nodes.len() > MAX_NODES {
            return Err(self.error(ROOT, "limit", format!("graph exceeds {MAX_NODES} nodes")));
        }
        let offset = self.nodes.len();
        for node in &mut other.nodes {
            match &mut node.kind {
                Kind::Seq(items) => items.iter_mut().for_each(|id| *id += offset),
                Kind::Map(entries) => entries.iter_mut().for_each(|(_, id)| *id += offset),
                Kind::Scalar { .. } => {}
            }
            if let Some(parent) = &mut node.parent {
                *parent += offset;
            }
        }
        self.nodes.append(&mut other.nodes);
        self.derived.reset();
        Ok(offset)
    }

    /// Record that the subtree grafted at `root` is the file `name`; its errors report it.
    pub fn set_source(&mut self, root: NodeId, name: String) {
        self.sources.insert(root, name);
        self.derived.reset();
    }

    /// The imported file `id` belongs to, if it is not part of the main program.
    pub fn source(&self, id: NodeId) -> Option<&str> {
        let mut top = id;
        while let Some(parent) = self.nodes[top].parent {
            top = parent;
        }
        self.sources.get(&top).map(String::as_str)
    }

    /// An error of `kind` blamed on node `id`, with its reference, span and file.
    pub fn error(&self, id: NodeId, kind: impl Into<String>, message: String) -> HelixError {
        HelixError {
            file: self.source(id).map(str::to_string),
            reference: Some(self.segments(id)),
            ..HelixError::unlocated(kind, message, self.span(id))
        }
    }

    /// A string identifier in a semantic declaration or call-head position.
    pub fn identifier(&self, id: NodeId) -> Option<&str> {
        match self.kind(id) {
            Kind::Scalar {
                value: Yaml::String(name),
                ..
            } => Some(name),
            _ => None,
        }
    }

    /// A statically named actor in an explicit call form.
    pub fn form(&self, id: NodeId) -> Option<&str> {
        self.identifier(*self.call(id)?.first()?)
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

#[derive(Clone)]
enum Anchor {
    Node(NodeId),
    Key(String),
}

#[derive(Default)]
struct Builder<'src> {
    anchors: std::collections::HashMap<usize, Anchor>,
    graph: Graph,
    source_lines: Vec<&'src str>,
    ends_with_break: bool,
    /// Open containers; for maps, `key` holds the pending key awaiting its value.
    stack: Vec<(NodeId, Option<String>)>,
    error: Option<HelixError>,
    tag_wrappers: Vec<NodeId>,
}

impl Builder<'_> {
    /// Record the first error; `on_event` ignores everything after it.
    fn fail(&mut self, kind: &'static str, message: impl Into<String>, at: Marker) {
        self.fail_hinted(kind, message, None, at);
    }

    fn fail_hinted(&mut self, kind: &'static str, message: impl Into<String>, hint: Option<String>, at: Marker) {
        self.error = Some(HelixError::unlocated(kind, message.into(), span(&at)).with_hint(hint));
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

    /// Anchors on tagged nodes name the entire normalized expression.
    fn anchor_node(&mut self, anchor: usize, id: NodeId) {
        let target = self.tag_wrappers.last().copied()
            .filter(|wrapper| self.graph.parent(id) == Some(*wrapper)).unwrap_or(id);
        self.anchors.insert(anchor, Anchor::Node(target));
    }

    /// Expand acyclic graph sharing at occurrence sites; anchor names carry no authority.
    fn alias(&mut self, anchor: usize, at: Marker) {
        let Some(target) = self.anchors.get(&anchor).cloned() else {
            return self.fail("parse", "unknown YAML alias", at);
        };
        let target = match target {
            Anchor::Key(key) => {
                if self.awaiting_key() {
                    let parent = self.stack.last().unwrap().0;
                    if matches!(self.graph.kind(parent), Kind::Map(entries) if entries.iter().any(|(name, _)| name == &key))
                    {
                        return self.fail("parse", "duplicate mapping key", at);
                    }
                    self.stack.last_mut().unwrap().1 = Some(key);
                } else {
                    self.add(
                        Kind::Scalar {
                            value: Yaml::String(key),
                        },
                        at,
                    );
                }
                return;
            }
            Anchor::Node(id) => id,
        };
        if self.stack.iter().any(|(id, _)| *id == target) {
            return self.fail("unsupported", "cyclic YAML aliases are not supported", at);
        }
        if self.awaiting_key() {
            let Kind::Scalar {
                value: Yaml::String(key),
            } = self.graph.kind(target)
            else {
                return self.fail("unsupported", "mapping keys must be strings", at);
            };
            let key = key.clone();
            let parent = self.stack.last().unwrap().0;
            if matches!(self.graph.kind(parent), Kind::Map(entries) if entries.iter().any(|(name, _)| name == &key))
            {
                return self.fail("parse", "duplicate mapping key", at);
            }
            self.stack.last_mut().unwrap().1 = Some(key);
            return;
        }
        enum Copy {
            Node(NodeId),
            Key(String),
            End,
        }
        let mut pending = vec![Copy::Node(target)];
        while let Some(next) = pending.pop() {
            match next {
                Copy::End => {
                    self.stack.pop();
                }
                Copy::Key(key) => self.stack.last_mut().unwrap().1 = Some(key),
                Copy::Node(source) => {
                    if self.graph.nodes.len() >= MAX_NODES {
                        return self.fail("limit", "alias expansion exceeds graph limit", at);
                    }
                    if self.stack.len() >= MAX_NESTING {
                        return self.fail(
                            "depth",
                            "alias expansion exceeds syntax nesting limit",
                            at,
                        );
                    }
                    match self.graph.kind(source).clone() {
                        Kind::Scalar { value } => {
                            self.add(Kind::Scalar { value }, at);
                        }
                        Kind::Seq(items) => {
                            let id = self.add(Kind::Seq(Vec::new()), at);
                            self.stack.push((id, None));
                            pending.push(Copy::End);
                            pending.extend(items.into_iter().rev().map(Copy::Node));
                        }
                        Kind::Map(entries) => {
                            let id = self.add(Kind::Map(Vec::new()), at);
                            self.stack.push((id, None));
                            pending.push(Copy::End);
                            for (key, child) in entries.into_iter().rev() {
                                pending.push(Copy::Node(child));
                                pending.push(Copy::Key(key));
                            }
                        }
                    }
                }
            }
        }
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
        let tagged = match &ev {
            Event::Scalar(_, _, _, tag) => tag.as_ref().map(|tag| (tag, None)),
            Event::SequenceStart(_, tag) => tag.as_ref().map(|tag| (tag, Some("seq"))),
            Event::MappingStart(_, tag) => tag.as_ref().map(|tag| (tag, Some("map"))),
            _ => None,
        };
        let local = tagged.and_then(|(tag, _)| {
            (tag.handle == "!" && matches!(tag.suffix.as_str(), "call" | "ref" | "literal"))
                .then_some(tag.suffix.as_str())
        });
        if let Some((tag, container)) = tagged {
            if local.is_some() {
                if self.awaiting_key() {
                    return self.fail("unsupported", "Helix tags cannot annotate mapping keys", at);
                }
                let shape = match container {
                    Some("seq") => "a sequence",
                    Some(_) => "a mapping",
                    None => "a scalar",
                };
                if local == Some("call") && container != Some("seq") {
                    return self.fail("parse", format!("`!call` requires a sequence such as `!call [add, 1, 2]`, got {shape}"), at);
                }
                if local == Some("ref") && container.is_some() {
                    return self.fail("parse", format!("`!ref` requires a scalar name such as `!ref x`, got {shape}"), at);
                }
            } else if tag.handle != "tag:yaml.org,2002:"
                || container.is_some_and(|expected| tag.suffix != expected)
                || (container.is_none()
                    && !matches!(tag.suffix.as_str(), "str" | "int" | "float" | "bool" | "null"))
            {
                let written = match tag.handle.as_str() {
                    "tag:yaml.org,2002:" => format!("!!{}", tag.suffix),
                    handle => format!("{handle}{}", tag.suffix),
                };
                let hint = match (tag.handle.as_str(), crate::builtins::arity(&tag.suffix)) {
                    ("!", Some(_)) => format!("`{}` is a builtin actor; write `!call [{}, ...]`", tag.suffix, tag.suffix),
                    _ => "Helix tags are `!call`, `!ref` and `!literal`; plain YAML values need no tag".into(),
                };
                return self.fail_hinted("unsupported", format!("unsupported YAML tag `{written}`"), Some(hint), at);
            }
        }
        if let Some(tag) = local {
            if self.graph.nodes.len() + 1 >= MAX_NODES || self.stack.len() + 1 >= MAX_NESTING {
                return self.fail("limit", "tag expansion exceeds syntax limits", at);
            }
            let wrapper = self.add(Kind::Map(Vec::new()), at);
            self.stack.push((wrapper, Some(format!("${tag}"))));
            self.tag_wrappers.push(wrapper);
        }
        if self.graph.nodes.len() >= MAX_NODES
            && matches!(
                ev,
                Event::Scalar(..) | Event::SequenceStart(..) | Event::MappingStart(..)
            )
        {
            return self.fail("limit", &format!("graph exceeds {MAX_NODES} nodes"), at);
        }
        let closes_node = matches!(ev, Event::Scalar(..) | Event::SequenceEnd | Event::MappingEnd);
        match ev {
            Event::Scalar(text, style, anchor, tag) => {
                let text = self.block_scalar(text, style, at);
                let plain = style == TScalarStyle::Plain;
                let value = match tag.as_ref().map(|tag| tag.suffix.as_str()) {
                    Some("str" | "ref") => Yaml::String(text.clone()),
                    Some("literal") if plain => infer(&text),
                    Some("literal") => Yaml::String(text.clone()),
                    Some(kind) => {
                        let value = infer(&text);
                        match (kind, value) {
                            ("int", value @ Yaml::Integer(_))
                            | ("bool", value @ Yaml::Boolean(_))
                            | ("null", value @ Yaml::Null) => value,
                            ("float", value @ Yaml::Real(_)) => value,
                            ("float", Yaml::Integer(value)) => Yaml::Real(format!("{value}.0")),
                            _ => {
                                return self.fail(
                                    "parse",
                                    "scalar does not match its YAML tag",
                                    at,
                                );
                            }
                        }
                    }
                    None if plain => infer(&text),
                    None => Yaml::String(text.clone()),
                };
                if matches!(value, Yaml::BadValue) {
                    return self.fail(
                        "unsupported",
                        "numeric scalar exceeds supported finite numeric range",
                        at,
                    );
                }
                if self.awaiting_key() {
                    let Yaml::String(key) = value else {
                        return self.fail("unsupported", "mapping keys must be strings", at);
                    };
                    let id = self.stack.last().unwrap().0;
                    if matches!(self.graph.kind(id), Kind::Map(entries) if entries.iter().any(|(other, _)| other == &key))
                    {
                        return self.fail("parse", "duplicate mapping key", at);
                    }
                    if anchor != 0 {
                        self.anchors.insert(anchor, Anchor::Key(key.clone()));
                    }
                    self.stack.last_mut().unwrap().1 = Some(key);
                    return;
                }
                let id = self.add(Kind::Scalar { value }, at);
                if anchor != 0 {
                    self.anchor_node(anchor, id);
                }
            }
            Event::SequenceStart(anchor, _) | Event::MappingStart(anchor, _) => {
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
                if anchor != 0 {
                    self.anchor_node(anchor, id);
                }
            }
            Event::SequenceEnd | Event::MappingEnd => {
                if let Some((id, _)) = self.stack.pop() {
                    if let Kind::Map(entries) = &mut self.graph.nodes[id].kind {
                        entries.sort_by(|a, b| a.0.cmp(&b.0));
                    }
                }
            }
            Event::DocumentStart if !self.graph.nodes.is_empty() => {
                self.fail("parse", "expected exactly one YAML document", at)
            }
            Event::Alias(anchor) => self.alias(anchor, at),
            _ => {}
        }
        if closes_node
            && self.tag_wrappers.last().is_some_and(|wrapper| self.stack.last().is_some_and(|(id, _)| id == wrapper))
        {
            self.stack.pop();
            self.tag_wrappers.pop();
        }
    }
}

/// YAML 1.2 core-schema inference for plain scalars (floats are kept verbatim as `Real`).
fn infer(text: &str) -> Yaml {
    match text {
        "" | "~" | "null" | "Null" | "NULL" => Yaml::Null,
        "true" | "True" | "TRUE" => Yaml::Boolean(true),
        "false" | "False" | "FALSE" => Yaml::Boolean(false),
        ".inf" | ".Inf" | ".INF" | "+.inf" | "+.Inf" | "+.INF" | "-.inf" | "-.Inf" | "-.INF"
        | ".nan" | ".NaN" | ".NAN" => Yaml::BadValue,
        _ => {
            if let Some(digits) = text.strip_prefix("0x") {
                if !digits.is_empty() && digits.bytes().all(|c| c.is_ascii_hexdigit()) {
                    return i64::from_str_radix(digits, 16)
                        .map(Yaml::Integer)
                        .unwrap_or(Yaml::BadValue);
                }
            }
            if let Some(digits) = text.strip_prefix("0o") {
                if !digits.is_empty() && digits.bytes().all(|c| (b'0'..=b'7').contains(&c)) {
                    return i64::from_str_radix(digits, 8)
                        .map(Yaml::Integer)
                        .unwrap_or(Yaml::BadValue);
                }
            }
            let digits = text.strip_prefix(['+', '-']).unwrap_or(text);
            if !digits.is_empty() && digits.bytes().all(|c| c.is_ascii_digit()) {
                return text
                    .parse::<i64>()
                    .map(Yaml::Integer)
                    .unwrap_or(Yaml::BadValue);
            }
            if let Ok(value) = text.parse::<f64>() {
                if text.bytes().any(|b| b.is_ascii_digit()) {
                    return if value.is_finite() {
                        Yaml::Real(text.to_string())
                    } else {
                        Yaml::BadValue
                    };
                }
            }
            Yaml::String(text.to_string())
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

pub(crate) fn render_location((source, segments): &crate::model::Location) -> crate::value::Value {
    use crate::model::{Segment, string as s};
    use crate::value::Value as V;
    V::Hash(
        [
            (s("source"), s(source)),
            (
                s("path"),
                V::Array(
                    segments
                        .iter()
                        .map(|segment| match segment {
                            Segment::Key(key) => V::String(key.clone()),
                            Segment::Index(index) => V::Integer(*index as i64),
                        })
                        .collect(),
                ),
            ),
        ]
        .into_iter()
        .collect(),
    )
}
