//! Validate the runtime class boundary; comparison and plans live in std:convergence.
use crate::value::Value;
use super::class::concrete_values;
use crate::builtins::fault::Fault;
use crate::model::PolicyCall;

pub(super) fn prepare(actor: &str, args: &[Value]) -> Result<PolicyCall, Fault> {
    let wanted = concrete_values(&args[0], 0)?;
    if !matches!(&args[1], Value::Hash(_)) {
        return Err(Fault::expected(1, "map of observed state", &args[1]));
    }
    Ok(PolicyCall {
        function: if actor == "drift" { "compare" } else { "plan" },
        args: vec![Value::Hash(wanted), args[1].clone()],
    })
}
