//! Helix: readable YAML is both program source and persisted VM state.

mod builtins;
mod emit;
mod eval;
mod graph;
mod library;
mod link;
mod vm;
mod value;
mod model;
mod inspect;

pub use library::names as std_modules;
pub use library::source as std_source;
pub use vm::{EffectRequest, Status, Vm};
pub use emit::{document as yaml_document, json_document};


pub mod tooling;
pub use vm::effects::{EffectHost, CommandPolicy, CommandProgress, ProgressCallback};
