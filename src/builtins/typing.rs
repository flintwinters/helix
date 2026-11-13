//! Types, classes and realization: the builtins that treat types as values. `ty` is the algebra
//! (parse, check, subtype), `construct` the constructors and reflection, `class` inheritance, and
//! `machine` comparison of a realized class with observed state.

mod class;
mod construct;
mod machine;
mod literal;
mod ty;

use crate::value::Value;

use super::fault::Fault;
pub use construct::refine;
pub(crate) use construct::{descriptor as describe, function_type};
pub(crate) use ty::Ty;
pub use ty::{is_primitive, violations, Ask, Pred, Violation};

pub(super) fn apply(actor: &str, args: &[Value], pred: &mut Pred) -> Result<Value, Fault> {
    match actor {
        "class" | "extend" | "inherit" | "realize" | "parents" | "open_fields" | "constrain" => class::apply(actor, args, pred),
        "drift" | "converge" => unreachable!("machine policy is scheduled by the evaluator"),
        _ => construct::apply(actor, args, pred),
    }
}

pub(super) fn prepare(actor: &str, args: &[Value], pred: &mut Pred) -> Result<Option<crate::model::PolicyCall>, Fault> {
    match actor {
        "drift" | "converge" => machine::prepare(actor, args).map(Some),
        _ => construct::prepare(actor, args, pred),
    }
}
