//! Realization as data: comparing a realized class (the intended machine) with an observed map
//! and describing what differs. Nothing here touches a machine; observed state is an argument.

use crate::value::Hash;
use crate::value::Value;

use super::class::concrete_values;
use super::ty::mapping;
use crate::builtins::fault::Fault;
use crate::builtins::value::equal;
use crate::model::string as s;

pub(super) fn apply(actor: &str, args: &[Value]) -> Result<Value, Fault> {
    let wanted = concrete_values(&args[0], 0)?;
    let Value::Hash(observed) = &args[1] else { return Err(Fault::expected(1, "map of observed state", &args[1])) };
    let drift = Drift::between(&wanted, observed);
    Ok(match actor {
        "drift" => drift.to_value(),
        "converge" => drift.plan(),
        _ => unreachable!("`{actor}` is not a machine builtin"),
    })
}

/// What the machine lacks, has wrong, and has beyond what its type says.
struct Drift {
    /// `(field, wanted, observed)`; `observed` is `None` when the machine lacks the field.
    unmet: Vec<(Value, Value, Option<Value>)>,
    extra: Hash,
}

impl Drift {
    fn between(wanted: &Hash, observed: &Hash) -> Drift {
        let unmet = wanted
            .iter()
            .filter(|(field, want)| !observed.get(*field).is_some_and(|have| equal(want, have)))
            .map(|(field, want)| (field.clone(), want.clone(), observed.get(field).cloned()))
            .collect();
        let extra = observed.iter().filter(|(field, _)| !wanted.contains_key(*field)).map(|(f, v)| (f.clone(), v.clone())).collect();
        Drift { unmet, extra }
    }

    /// `{missing: {field: wanted}, changed: {field: {want, have}}, extra: {field: have}}`.
    fn to_value(&self) -> Value {
        let (mut missing, mut changed) = (Hash::new(), Hash::new());
        for (field, want, have) in &self.unmet {
            match have {
                None => drop(missing.insert(field.clone(), want.clone())),
                Some(have) => drop(changed.insert(field.clone(), mapping([("want", want.clone()), ("have", have.clone())]))),
            }
        }
        mapping([("missing", Value::Hash(missing)), ("changed", Value::Hash(changed)), ("extra", Value::Hash(self.extra.clone()))])
    }

    /// Ordered steps that make the machine match: one `set` per unmet field. Extra state is only
    /// reported, never removed, so a plan against a conforming machine is empty (idempotent).
    fn plan(&self) -> Value {
        Value::Array(
            self.unmet
                .iter()
                .map(|(field, want, have)| match have {
                    None => mapping([("op", s("set")), ("field", field.clone()), ("to", want.clone())]),
                    Some(have) => mapping([("op", s("set")), ("field", field.clone()), ("from", have.clone()), ("to", want.clone())]),
                })
                .collect(),
        )
    }
}
