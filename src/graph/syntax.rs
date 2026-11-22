//! The single boundary between YAML representation nodes and executable language forms.
//! Presentation style and mapping insertion order never select behavior.
use crate::model::{Graph, Kind, NodeId};
use yaml_rust2::Yaml;

pub(crate) enum Syntax<'a> {
    Scalar(&'a Yaml),
    Sequence(&'a [NodeId]),
    Mapping(&'a [(String, NodeId)]),
    Call(&'a [NodeId]),
    Reference(&'a str),
    Literal(NodeId),
    Invalid(&'static str),
}

/// A node's syntactic class, cached per node by `derived.rs`. Payloads name the child node
/// whose kind the class was decided on, so `syntax` reads slices without repeating key tests.
#[derive(Clone, Copy)]
pub(crate) enum Class {
    Scalar,
    Sequence,
    Mapping,
    /// The `$call` value: a nonempty sequence.
    Call(NodeId),
    /// The `$ref` value: a nonempty string scalar.
    Reference(NodeId),
    Literal(NodeId),
    Invalid(&'static str),
}

impl Graph {
    pub(crate) fn syntax(&self, id: NodeId) -> Syntax<'_> {
        match (self.class(id), self.kind(id)) {
            (Class::Scalar, Kind::Scalar { value }) => Syntax::Scalar(value),
            (Class::Sequence, Kind::Seq(items)) => Syntax::Sequence(items),
            (Class::Mapping, Kind::Map(entries)) => Syntax::Mapping(entries),
            (Class::Call(value), _) => match self.kind(value) {
                Kind::Seq(items) => Syntax::Call(items),
                _ => unreachable!("a `$call` class names a sequence"),
            },
            (Class::Reference(value), _) => match self.kind(value) {
                Kind::Scalar { value: Yaml::String(name) } => Syntax::Reference(name),
                _ => unreachable!("a `$ref` class names a string"),
            },
            (Class::Literal(value), _) => Syntax::Literal(value),
            (Class::Invalid(message), _) => Syntax::Invalid(message),
            _ => unreachable!("a node's class is decided from its kind"),
        }
    }

    /// Uncached classification; `derived.rs` memoizes it per node.
    pub(crate) fn classify(&self, id: NodeId) -> Class {
        match self.kind(id) {
            Kind::Scalar { .. } => Class::Scalar,
            Kind::Seq(_) => Class::Sequence,
            Kind::Map(entries) => {
                if let [(key, value)] = entries.as_slice() {
                    match key.as_str() {
                        "$call" => {
                            return match self.kind(*value) {
                                Kind::Seq(items) if !items.is_empty() => Class::Call(*value),
                                _ => Class::Invalid("$call requires a nonempty sequence"),
                            };
                        }
                        "$ref" => {
                            return match self.kind(*value) {
                                Kind::Scalar {
                                    value: Yaml::String(name),
                                    ..
                                } if !name.is_empty() => Class::Reference(*value),
                                _ => Class::Invalid("$ref requires a nonempty string"),
                            };
                        }
                        "$literal" => return Class::Literal(*value),
                        _ => {}
                    }
                }
                Class::Mapping
            }
        }
    }

    pub(crate) fn call(&self, id: NodeId) -> Option<&[NodeId]> {
        match self.syntax(id) {
            Syntax::Call(items) => Some(items),
            _ => None,
        }
    }

    /// The semantic parent skips the sequence carrying a call's operands.
    pub(crate) fn lexical_parent(&self, id: NodeId) -> Option<NodeId> {
        let parent = self.parent(id)?;
        if let Some(wrapper) = self.parent(parent) {
            // A call wrapper has exactly one child, its operand sequence.
            if self.call(wrapper).is_some() {
                return Some(wrapper);
            }
        }
        Some(parent)
    }
}
