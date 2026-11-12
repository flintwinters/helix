//! Type constructors and reflection: ordinary pure builtins over type values. A type is a string
//! naming a primitive or a map with a `type` key, so it prints, diffs and re-reads like any value.

use crate::value::Hash;
use crate::value::Value;

use super::ty::{holds, mapping, subtype, text, violations, Field, Pred, Ty, Violation};
use crate::builtins::fault::Fault;
use crate::model::string as s;

/// `{type: kind, ..entries}`.
pub(super) fn descriptor<const N: usize>(kind: &str, entries: [(&str, Value); N]) -> Value {
    let mut map = Hash::new();
    map.insert(s("type"), s(kind));
    for (k, v) in entries {
        map.insert(s(k), v);
    }
    Value::Hash(map)
}

/// The argument at `at` as a validated type, or a fault blaming it.
fn type_arg(args: &[Value], at: usize) -> Result<Ty, Fault> {
    Ty::parse(&args[at]).map_err(|why| Fault::at(at, "type", why))
}

fn spec_arg(args: &[Value], at: usize) -> Result<Field, Fault> {
    Field::parse(&args[at]).map_err(|why| Fault::at(at, "type", why))
}

pub(super) fn apply(actor: &str, args: &[Value], pred: &mut Pred) -> Result<Value, Fault> {
    match actor {
        "fields" => reflect(&args[0], "fields"),
        "is" => Ok(Value::Boolean(violations_arg(args, pred)?.is_empty())),
        "violations" => Ok(Value::Array(violations_arg(args, pred)?.iter().map(Violation::to_value).collect())),
        "expect" => {
            let found = violations_arg(args, pred)?;
            if found.is_empty() {
                return Ok(args[0].clone());
            }
            let plural = if found.len() == 1 { "" } else { "s" };
            let message = format!("{} type violation{plural}: {}", found.len(), found[0].message());
            let data = mapping([("violations", Value::Array(found.iter().map(Violation::to_value).collect()))]);
            Err(Fault { cause: None, kind: "type".into(), message, arg: Some(0), data: Some(data) })
        }
        "subtype" => Ok(Value::Boolean(subtype(&type_arg(args, 0)?, &type_arg(args, 1)?, pred))),
        _ => unreachable!("`{actor}` is not a type builtin"),
    }
}

fn violations_arg(args: &[Value], pred: &mut Pred) -> Result<Vec<Violation>, Fault> {
    violations(&args[0], &args[1], pred).map_err(|why| Fault::at(1, "type", why))
}

/// `fields` / `parents` of a record or class type, as the data it was declared with.
pub(super) fn reflect(ty: &Value, member_name: &str) -> Result<Value, Fault> {
    match ty {
        Value::Hash(map) if matches!(text(map, "type"), Some("record" | "class")) => map.get(&s(member_name)).cloned().ok_or_else(|| Fault::at(0, "type", format!("this type has no `{member_name}`"))),
        other => Err(Fault::expected(0, "a record or class type", other)),
    }
}

/// `{type: refine, of: base, check: <fn source>}`; `base` must be a type.
pub fn refine(base: Value, check: Value) -> Result<Value, Fault> {
    Ty::parse(&base).map_err(|why| Fault::at(0, "type", why))?;
    Ok(descriptor("refine", [("of", base), ("check", check)]))
}

/// Validate constructor arguments through the same type parser/checker as annotations.
/// Descriptor assembly and immutable transformation belong to std:type_descriptor.
pub(super) fn prepare(actor: &str, args: &[Value], pred: &mut Pred) -> Result<Option<crate::model::PolicyCall>, Fault> {
    match actor {
        "list_of" | "map_of" | "optional" => { type_arg(args, 0)?; }
        "default" => {
            let ty = type_arg(args, 0)?;
            if !holds(&args[1], &ty, pred) {
                return Err(Fault::at(1, "type", format!("default does not fit {}", ty.name())));
            }
        }
        "union" => { (0..args.len()).try_for_each(|i| type_arg(args, i).map(drop))?; }
        "enum" | "literal" => {}
        "secret" => match &args[0] {
            Value::String(reference) if !reference.is_empty() => {}
            other => return Err(Fault::expected(0, "a non-empty string reference", other)),
        },
        "record" => {
            if args.len() % 2 != 0 {
                return Err(Fault::whole("arity", "`record` takes alternating field names and types".into()));
            }
            let mut names = std::collections::BTreeSet::new();
            for (i, pair) in args.chunks_exact(2).enumerate() {
                let Value::String(name) = &pair[0] else { return Err(Fault::expected(2 * i, "string field name", &pair[0])) };
                spec_arg(args, 2 * i + 1)?;
                if !names.insert(name) {
                    return Err(Fault::at(2 * i, "type", format!("duplicate field `{name}`")));
                }
            }
        }
        "with_field" => {
            let Value::Hash(record) = &args[0] else { return Err(Fault::expected(0, "a record type", &args[0])) };
            if text(record, "type") != Some("record") {
                return Err(Fault::at(0, "type", "`with_field` extends a record; classes grow with `extend`".into()));
            }
            if !matches!(&args[1], Value::String(_)) { return Err(Fault::expected(1, "string field name", &args[1])); }
            spec_arg(args, 2)?;
            if !matches!(record.get(&s("fields")), Some(Value::Hash(_))) {
                return Err(Fault::at(0, "type", "malformed record type".into()));
            }
        }
        _ => return Ok(None),
    }
    Ok(Some(crate::model::PolicyCall {
        function: "build",
        args: vec![s(actor), Value::Array(args.to_vec().into())],
    }))
}
