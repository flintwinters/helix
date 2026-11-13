//! An enum member denotes every equal value, not just its stored representation.
//! Numeric alternatives are bounded; collection proofs factor through structural targets.
//! Arbitrary predicates over non-singleton collections remain unproved rather than enumerating
//! exponentially many combinations. Function and machine equality is not singleton identity.
use super::ty::{holds, record_subtype, Field, Pred, Ty};
use crate::builtins::value::equal;
use crate::model::string as s;
use crate::value::{Hash, Value};

fn numbers(value: &Value) -> Option<Vec<Value>> {
    let alternate = match value {
        Value::Integer(number) => Value::Float(*number as f64),
        Value::Float(number) => Value::Integer(*number as i64),
        _ => return None,
    };
    let mut variants = vec![value.clone()];
    if equal(value, &alternate) {
        variants.push(alternate);
    }
    match value {
        Value::Integer(0) => variants.push(Value::Float(-0.0)),
        Value::Float(number) if *number == 0.0 => variants.push(Value::Float(-number)),
        _ => {}
    }
    Some(variants)
}

fn singleton(value: &Value) -> bool {
    match value {
        Value::Integer(_) | Value::Float(_) => numbers(value).unwrap().len() == 1,
        Value::Array(items) => items.iter().all(singleton),
        Value::Hash(items) => items.values().all(singleton),
        Value::Function(_) | Value::Machine(_) => false,
        _ => true,
    }
}

fn record_fields(values: &Hash) -> Vec<(String, Field)> {
    values.iter().map(|(name, value)| (name.as_str().unwrap().to_owned(),
        Field { ty: Ty::Enum(vec![value.clone()]), required: true, default: None })).collect()
}

/// Every representation equal to this member is accepted by the target.
pub(super) fn covered_by(value: &Value, target: &Ty, pred: &mut Pred) -> bool {
    if let Some(variants) = numbers(value) {
        return variants.iter().all(|variant| holds(variant, target, pred));
    }
    match (value, target) {
        (_, Ty::Any) => true,
        (_, Ty::Enum(values)) => values.iter().any(|member| equal(value, member)),
        (_, Ty::Union(members)) => members.iter().any(|member| covered_by(value, member, pred)),
        (Value::Array(items), Ty::List(element)) => items.iter().all(|item| covered_by(item, element, pred)),
        (Value::Hash(items), Ty::Map(element)) => items.values().all(|item| covered_by(item, element, pred)),
        (Value::Hash(items), Ty::Record(fields)) => record_subtype(&record_fields(items), fields, pred),
        (Value::Hash(items), Ty::Class(fields, fixed, constraints)) if constraints.is_empty() => {
            record_subtype(&record_fields(items), fields, pred)
                && fixed.iter().all(|(name, pinned)| items.get(&s(name)).is_some_and(|v| equal(v, pinned)))
        }
        (Value::Function(_), Ty::Function) | (Value::Machine(_), Ty::Vm) => true,
        _ => singleton(value) && holds(value, target, pred),
    }
}

/// A failed coverage proof is not disjointness: retain possible structural intersections.
pub(super) fn may_overlap(value: &Value, target: &Ty) -> bool {
    match target {
        Ty::Any => return true,
        Ty::Never => return false,
        Ty::Refine(base, _) => return may_overlap(value, base),
        Ty::Union(members) => return members.iter().any(|member| may_overlap(value, member)),
        Ty::Enum(values) => return values.iter().any(|member| equal(value, member)),
        _ => {}
    }
    if let Some(variants) = numbers(value) {
        return variants.iter().any(|variant| holds(variant, target, &mut |_| Err("static".into())));
    }
    match (value, target) {
        (Value::Array(_), Ty::List(_)) => true,
        (Value::Hash(_), Ty::Map(_) | Ty::Record(_) | Ty::Class(..)) => true,
        (Value::Function(_), Ty::Function | Ty::Fn(..)) | (Value::Machine(_), Ty::Vm) => true,
        _ => holds(value, target, &mut |_| Err("static".into())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn maps_overlap_records_and_classes_with_a_shared_empty_member() {
        let map = Ty::Map(Box::new(Ty::Int));
        let empty = Value::Hash(Hash::new());
        for structured in [Ty::Record(Vec::new()), Ty::Class(Vec::new(), Vec::new(), Vec::new())] {
            assert!(holds(&empty, &map, &mut |_| Err("no predicate expected".into())));
            assert!(holds(&empty, &structured, &mut |_| Err("no predicate expected".into())));
            assert!(map.overlaps(&structured));
            assert!(structured.overlaps(&map));
        }
    }
}
