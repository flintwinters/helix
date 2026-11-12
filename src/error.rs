//! Structured errors: the YAML shape of an error is part of the public contract.

use yaml_rust2::Yaml;
use yaml_rust2::yaml::Hash;

use crate::span::Span;

pub struct HelixError {
    /// Stable machine-readable category; the full list is in `LANGUAGE.md`.
    pub kind: String,
    pub message: String,
    /// The imported file the error lies in; absent for the program itself. `path`, `line` and
    /// `column` are relative to that file.
    pub file: Option<String>,
    /// Root-relative object path of the offending node; absent for parse errors.
    pub path: Option<String>,
    pub span: Span,
    /// Plain-value payload attached by user code via `raise`.
    pub data: Option<Yaml>,
    /// Call sites ordered from the innermost invocation outward.
    pub(crate) calls: Vec<Yaml>,
    /// Complete original error supplied to a map-form re-raise.
    pub(crate) cause: Option<Yaml>,
}

impl HelixError {
    pub fn to_yaml(&self) -> Yaml {
        let mut h = Hash::new();
        h.insert(s("kind"), s(&self.kind));
        h.insert(s("message"), s(&self.message));
        if let Some(file) = &self.file {
            h.insert(s("file"), s(file));
        }
        if let Some(path) = &self.path {
            h.insert(s("path"), s(path));
        }
        h.insert(s("line"), Yaml::Integer(self.span.line as i64));
        h.insert(s("column"), Yaml::Integer(self.span.col as i64));
        if let Some(data) = &self.data {
            h.insert(s("data"), data.clone());
        }
        if !self.calls.is_empty() {
            h.insert(s("calls"), Yaml::Array(self.calls.clone()));
        }
        if let Some(cause) = &self.cause { h.insert(s("cause"), cause.clone()); }
        Yaml::Hash(h)
    }
}

pub(crate) fn s(text: &str) -> Yaml {
    Yaml::String(text.to_string())
}
