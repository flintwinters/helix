//! Types, classes and realization: the builtins that treat types as values. `ty` is the algebra
//! (parse, check, subtype), `construct` the constructors and reflection, `class` inheritance, and
//! `machine` comparison of a realized class with observed state.

mod class;
mod construct;
mod machine;
mod ty;

use crate::value::Value;

use super::fault::Fault;
pub use construct::refine;
pub use ty::{is_primitive, violations, Pred, Violation};

pub(super) fn apply(actor: &str, args: &[Value], pred: &mut Pred) -> Result<Value, Fault> {
    match actor {
        "class" | "extend" | "inherit" | "realize" | "parents" | "open_fields" | "constrain" => class::apply(actor, args, pred),
        "drift" | "converge" => machine::apply(actor, args),
        _ => construct::apply(actor, args, pred),
    }
}
