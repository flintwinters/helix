//! Structured errors: the YAML shape of an error is part of the public contract.

use yaml_rust2::Yaml;
use yaml_rust2::yaml::Hash;

use crate::span::Span;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HelixError {
    /// Stable machine-readable category; the full list is in `LANGUAGE.md`.
    pub kind: &'static str,
    pub message: String,
    /// Root-relative object path of the offending node; absent for parse errors.
    pub path: Option<String>,
    pub span: Span,
}

impl HelixError {
    pub fn to_yaml(&self) -> Yaml {
        let mut h = Hash::new();
        h.insert(s("kind"), s(self.kind));
        h.insert(s("message"), s(&self.message));
        if let Some(path) = &self.path {
            h.insert(s("path"), s(path));
        }
        h.insert(s("line"), Yaml::Integer(self.span.line as i64));
        h.insert(s("column"), Yaml::Integer(self.span.col as i64));
        Yaml::Hash(h)
    }
}

pub(crate) fn s(text: &str) -> Yaml {
    Yaml::String(text.to_string())
}
