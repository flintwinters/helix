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

impl Graph {
    pub(crate) fn syntax(&self, id: NodeId) -> Syntax<'_> {
        match self.kind(id) {
            Kind::Scalar { value, .. } => Syntax::Scalar(value),
            Kind::Seq(items) => Syntax::Sequence(items),
            Kind::Map(entries) => {
                if let [(key, value)] = entries.as_slice() {
                    match key.as_str() {
                        "$call" => {
                            return match self.kind(*value) {
                                Kind::Seq(items) if !items.is_empty() => Syntax::Call(items),
                                _ => Syntax::Invalid("$call requires a nonempty sequence"),
                            };
                        }
                        "$ref" => {
                            return match self.kind(*value) {
                                Kind::Scalar {
                                    value: Yaml::String(name),
                                    ..
                                } if !name.is_empty() => Syntax::Reference(name),
                                _ => Syntax::Invalid("$ref requires a nonempty string"),
                            };
                        }
                        "$literal" => return Syntax::Literal(*value),
                        _ => {}
                    }
                }
                Syntax::Mapping(entries)
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
            if self.call(wrapper).is_some_and(|items| items.contains(&id)) {
                return Some(wrapper);
            }
        }
        Some(parent)
    }
}
