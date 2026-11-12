//! Source locations shared by the graph and by structured errors.

/// 1-indexed source location.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Span {
    pub line: usize,
    pub col: usize,
}
