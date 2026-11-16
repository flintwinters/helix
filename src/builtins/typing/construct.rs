//! Type constructors and reflection: ordinary pure builtins over type values. A type is a string
//! naming a primitive or a map with a `type` key, so it prints, diffs and re-reads like any value.

use crate::value::Hash;
use crate::value::Value;

use super::ty::{check_ty, validate_defaults, mapping, subtype, text, violations, Field, Pred, Ty, Violation};
use crate::builtins::fault::Fault;
use crate::model::string as s;

/// `{type: kind, ..entries}`.
pub(crate) fn descriptor<const N: usize>(kind: &str, entries: [(&str, Value); N]) -> Value {
    let mut map = Hash::new();
    map.insert(s("type"), s(kind));
    for (k, v) in entries {
        map.insert(s(k), v);
    }
    Value::Hash(map)
}

/// The argument at `at` as a validated type, or a fault blaming it.
fn type_arg(args: &[Value], at: usize) -> Result<Ty, Fault> {
    Ty::parse_open(&args[at]).map_err(|why| Fault::at(at, "type", why))
}

fn spec_arg(args: &[Value], at: usize, pred: &mut Pred) -> Result<Field, Fault> {
    let field = Field::parse(&args[at]).map_err(|why| Fault::at(at, "type", why))?;
    validate_field(&field, at, pred)?;
    Ok(field)
}

pub(super) fn validate_field(field: &Field, arg: usize, pred: &mut Pred) -> Result<(), Fault> {
    let found = validate_defaults(&Ty::Record(vec![("field".into(), field.clone())]), pred);
    predicate_fault(&found, arg)?;
    if found.is_empty() { return Ok(()); }
    Err(Fault { cause: None, kind: "type".into(), message: format!("invalid declared default: {}", found[0].message()),
        arg: Some(arg), data: Some(mapping([("violations", Value::Array(found.iter().map(Violation::to_value).collect()))])) })
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
        // Mutual subtyping: exactly as strong as `subtype`, so `false` also means unproved.
        "equivalent" => {
            let (a, b) = (type_arg(args, 0)?, type_arg(args, 1)?);
            Ok(Value::Boolean(subtype(&a, &b, pred) && subtype(&b, &a, pred)))
        }
        _ => unreachable!("`{actor}` is not a type builtin"),
    }
}

fn violations_arg(args: &[Value], pred: &mut Pred) -> Result<Vec<Violation>, Fault> {
    let found = violations(&args[0], &args[1], pred).map_err(|why| Fault::at(1, "type", why))?;
    predicate_fault(&found, 1)?;
    if found.iter().any(Violation::is_declaration) {
        return Err(Fault { cause: None, kind: "type".into(), message: "invalid declared default".into(), arg: Some(1),
            data: Some(mapping([("violations", Value::Array(found.iter().map(Violation::to_value).collect()))])) });
    }
    Ok(found)
}

fn predicate_fault(found: &[Violation], arg: usize) -> Result<(), Fault> {
    if let Some(broken) = found.iter().find(|v| v.rule() == "predicate_error") {
        return Err(Fault { kind: "predicate".into(), message: broken.message().into(), arg: Some(arg),
            data: Some(mapping([("violations", Value::Array(found.iter().map(Violation::to_value).collect()))])), cause: None });
    }
    Ok(())
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
    Ty::parse_open(&base).map_err(|why| Fault::at(0, "type", why))?;
    Ok(descriptor("refine", [("of", base), ("check", check)]))
}

/// `{type: function, params, result}` (plus `pure: true` when effect-free): the signature a function
/// declares with its annotations.
pub(crate) fn function_type(params: Vec<Value>, result: Value, pure: bool) -> Value {
    let signature = descriptor("function", [("params", Value::Array(params.into())), ("result", result)]);
    match (signature, pure) {
        (Value::Hash(mut map), true) => {
            map.insert(s("pure"), Value::Boolean(true));
            Value::Hash(map)
        }
        (signature, _) => signature,
    }
}

/// Validate constructor arguments through the same type parser/checker as annotations.
/// Descriptor assembly and immutable transformation belong to std:type_descriptor.
pub(super) fn prepare(actor: &str, args: &[Value], pred: &mut Pred) -> Result<Option<crate::model::PolicyCall>, Fault> {
    match actor {
        "list_of" | "optional" => { type_arg(args, 0)?; }
        "map_of" => { (0..args.len()).try_for_each(|i| type_arg(args, i).map(drop))?; }
        "default" => {
            let ty = type_arg(args, 0)?;
            let found = check_ty(&args[1], &ty, pred);
            predicate_fault(&found, 0)?;
            if !found.is_empty() {
                return Err(Fault::at(1, "type", format!("default does not fit {}", ty.name())));
            }
        }
        "function_of" | "pure_function_of" => {
            let Value::Array(params) = &args[0] else { return Err(Fault::expected(0, "a list of parameter types", &args[0])) };
            params.iter().try_for_each(|param| Ty::parse_open(param).map(drop)).map_err(|why| Fault::at(0, "type", why))?;
            type_arg(args, 1)?;
        }
        "union" | "intersection" | "tuple_of" => { (0..args.len()).try_for_each(|i| type_arg(args, i).map(drop))?; }
        "rec" => {
            let Value::String(name) = &args[0] else { return Err(Fault::expected(0, "a string type variable name", &args[0])) };
            if name.is_empty() { return Err(Fault::at(0, "type", "a type variable name must not be empty".into())); }
            let body = type_arg(args, 1)?;
            Ty::Rec(name.clone(), Box::new(body)).close(&mut Vec::new(), &mut Vec::new()).map_err(|why| Fault::at(1, "type", why))?;
        }
        "var" => match &args[0] {
            Value::String(name) if !name.is_empty() => {}
            other => return Err(Fault::expected(0, "a non-empty string type variable name", other)),
        },
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
                spec_arg(args, 2 * i + 1, pred)?;
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
            spec_arg(args, 2, pred)?;
            let Some(Value::Hash(fields)) = record.get(&s("fields")) else {
                return Err(Fault::at(0, "type", "malformed record type".into()));
            };
            // The replacement may repair a malformed removed field, but every
            // surviving declaration must meet the same contract as `record`.
            // Validate surviving defaults through the same predicate engine.
            for (_, spec) in fields.iter().filter(|(name, _)| *name != &args[1]) {
                let field = Field::parse(spec).map_err(|why| Fault::at(0, "type", why))?;
                validate_field(&field, 0, pred)?;
            }
        }
        _ => return Ok(None),
    }
    Ok(Some(crate::model::PolicyCall {
        function: "build",
        args: vec![s(actor), Value::Array(args.to_vec().into())],
    }))
}
