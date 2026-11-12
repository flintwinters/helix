//! Type constructors and reflection: ordinary pure builtins over type values. A type is a string
//! naming a primitive or a map with a `type` key, so it prints, diffs and re-reads like any value.

use yaml_rust2::yaml::Hash;
use yaml_rust2::Yaml;

use super::ty::{holds, mapping, subtype, text, violations, Field, Pred, Ty, Violation};
use crate::builtins::fault::Fault;
use crate::error::s;

/// `{type: kind, ..entries}`.
pub(super) fn descriptor<const N: usize>(kind: &str, entries: [(&str, Yaml); N]) -> Yaml {
    let mut map = Hash::new();
    map.insert(s("type"), s(kind));
    for (k, v) in entries {
        map.insert(s(k), v);
    }
    Yaml::Hash(map)
}

/// The argument at `at` as a validated type, or a fault blaming it.
fn type_arg(args: &[Yaml], at: usize) -> Result<Ty, Fault> {
    Ty::parse(&args[at]).map_err(|why| Fault::at(at, "type", why))
}

fn spec_arg(args: &[Yaml], at: usize) -> Result<Field, Fault> {
    Field::parse(&args[at]).map_err(|why| Fault::at(at, "type", why))
}

pub(super) fn apply(actor: &str, args: &[Yaml], pred: &mut Pred) -> Result<Yaml, Fault> {
    match actor {
        "list_of" | "map_of" => {
            type_arg(args, 0)?;
            Ok(descriptor(if actor == "list_of" { "list" } else { "map" }, [("of", args[0].clone())]))
        }
        "optional" => {
            type_arg(args, 0)?;
            Ok(descriptor("optional", [("of", args[0].clone())]))
        }
        "default" => {
            let ty = type_arg(args, 0)?;
            if !holds(&args[1], &ty, pred) {
                return Err(Fault::at(1, "type", format!("default does not fit {}", ty.name())));
            }
            Ok(descriptor("default", [("of", args[0].clone()), ("default", args[1].clone())]))
        }
        "union" => {
            (0..args.len()).try_for_each(|i| type_arg(args, i).map(drop))?;
            Ok(descriptor("union", [("of", Yaml::Array(args.to_vec()))]))
        }
        "enum" => Ok(descriptor("enum", [("values", Yaml::Array(args.to_vec()))])),
        "literal" => Ok(descriptor("literal", [("value", args[0].clone())])),
        "record" => record(args),
        "with_field" => with_field(args),
        "fields" => reflect(&args[0], "fields"),
        "is" => Ok(Yaml::Boolean(violations_arg(args, pred)?.is_empty())),
        "violations" => Ok(Yaml::Array(violations_arg(args, pred)?.iter().map(Violation::to_yaml).collect())),
        "expect" => {
            let found = violations_arg(args, pred)?;
            if found.is_empty() {
                return Ok(args[0].clone());
            }
            let plural = if found.len() == 1 { "" } else { "s" };
            let message = format!("{} type violation{plural}: {}", found.len(), found[0].message());
            let data = mapping([("violations", Yaml::Array(found.iter().map(Violation::to_yaml).collect()))]);
            Err(Fault { kind: "type".into(), message, arg: Some(0), data: Some(data) })
        }
        "subtype" => Ok(Yaml::Boolean(subtype(&type_arg(args, 0)?, &type_arg(args, 1)?, pred))),
        "secret" => match &args[0] {
            Yaml::String(reference) if !reference.is_empty() => Ok(descriptor("secret", [("ref", args[0].clone())])),
            other => Err(Fault::expected(0, "a non-empty string reference", other)),
        },
        _ => unreachable!("`{actor}` is not a type builtin"),
    }
}

fn violations_arg(args: &[Yaml], pred: &mut Pred) -> Result<Vec<Violation>, Fault> {
    violations(&args[0], &args[1], pred).map_err(|why| Fault::at(1, "type", why))
}

/// `fields` / `parents` of a record or class type, as the data it was declared with.
pub(super) fn reflect(ty: &Yaml, member_name: &str) -> Result<Yaml, Fault> {
    match ty {
        Yaml::Hash(map) if matches!(text(map, "type"), Some("record" | "class")) => map.get(&s(member_name)).cloned().ok_or_else(|| Fault::at(0, "type", format!("this type has no `{member_name}`"))),
        other => Err(Fault::expected(0, "a record or class type", other)),
    }
}

/// `[with_field, record, name, spec]`: the record with `name` added, or replaced in place.
fn with_field(args: &[Yaml]) -> Result<Yaml, Fault> {
    let Yaml::Hash(record) = &args[0] else { return Err(Fault::expected(0, "a record type", &args[0])) };
    if text(record, "type") != Some("record") {
        return Err(Fault::at(0, "type", "`with_field` extends a record; classes grow with `extend`".into()));
    }
    let Yaml::String(name) = &args[1] else { return Err(Fault::expected(1, "string field name", &args[1])) };
    spec_arg(args, 2)?;
    let Some(Yaml::Hash(fields)) = record.get(&s("fields")) else { return Err(Fault::at(0, "type", "malformed record type".into())) };
    let mut fields = fields.clone();
    match fields.get_mut(&s(name)) {
        Some(slot) => *slot = args[2].clone(),
        None => drop(fields.insert(s(name), args[2].clone())),
    }
    Ok(descriptor("record", [("fields", Yaml::Hash(fields))]))
}

/// `{type: refine, of: base, check: <fn source>}`; `base` must be a type.
pub fn refine(base: Yaml, check: Yaml) -> Result<Yaml, Fault> {
    Ty::parse(&base).map_err(|why| Fault::at(0, "type", why))?;
    Ok(descriptor("refine", [("of", base), ("check", check)]))
}

/// `[record, name, type, name, type, ...]`: names are strings, each type a field spec.
fn record(args: &[Yaml]) -> Result<Yaml, Fault> {
    if args.len() % 2 != 0 {
        return Err(Fault::whole("arity", "`record` takes alternating field names and types".into()));
    }
    let mut fields = Hash::new();
    for (i, pair) in args.chunks_exact(2).enumerate() {
        let Yaml::String(name) = &pair[0] else { return Err(Fault::expected(2 * i, "string field name", &pair[0])) };
        spec_arg(args, 2 * i + 1)?;
        if fields.insert(s(name), pair[1].clone()).is_some() {
            return Err(Fault::at(2 * i, "type", format!("duplicate field `{name}`")));
        }
    }
    Ok(descriptor("record", [("fields", Yaml::Hash(fields))]))
}

