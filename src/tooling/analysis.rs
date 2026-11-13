//! Non-executing source tools and their LSP adapter share the same analysis passes.
mod body;
mod lsp;
mod source;

pub use lsp::serve as lsp;
pub use source::{check, format, schema};
