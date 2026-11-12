//! The type algebra: parsing a type value into `Ty`, checking values against it (collecting every
//! violation), and subtyping. Nothing here converts or repairs a value; it only reports where a
//! value does not fit.

use yaml_rust2::yaml::Hash;
use yaml_rust2::Yaml;

use crate::builtins::value::{equal, type_name};
use crate::error::s;

/// Runs a refinement's `[fn, [value], body]` source on a value. `Err` is a failure inside the
/// predicate, reported as a violation rather than aborting the check.
pub type Pred<'a> = dyn FnMut(&Yaml, &Yaml) -> Result<bool, String> + 'a;

const PRIMITIVES: [&str; 9] = ["any", "integer", "float", "string", "boolean", "null", "list", "map", "secret"];

/// Whether `name` is a primitive type, which evaluates as a bare symbol when nothing shadows it.
pub fn is_primitive(name: &str) -> bool {
    PRIMITIVES.contains(&name)
}

#[derive(Clone, Debug, PartialEq)]
pub(super) enum Ty {
    Any,
    Int,
    Float,
    Str,
    Bool,
    Null,
    Secret,
    List(Box<Ty>),
    Map(Box<Ty>),
    /// Closed: a value may not carry fields the record does not declare.
    Record(Vec<(String, Field)>),
    Union(Vec<Ty>),
    Enum(Vec<Yaml>),
    /// A base type plus the raw source of a predicate.
    Refine(Box<Ty>, Yaml),
}

#[derive(Clone, Debug, PartialEq)]
pub(super) struct Field {
    pub ty: Ty,
    pub required: bool,
    pub default: Option<Yaml>,
}

/// A place where a value does not fit its type: `at` is the path inside the checked value.
pub struct Violation {
    at: String,
    message: String,
}

pub(super) fn mapping<const N: usize>(entries: [(&str, Yaml); N]) -> Yaml {
    Yaml::Hash(entries.into_iter().map(|(k, v)| (s(k), v)).collect())
}

impl Violation {
    pub fn at(&self) -> &str {
        &self.at
    }

    pub fn message(&self) -> &str {
        &self.message
    }

    pub fn to_yaml(&self) -> Yaml {
        mapping([("at", s(&self.at)), ("message", s(&self.message))])
    }
}


pub(super) fn text<'a>(map: &'a Hash, key: &str) -> Option<&'a str> {
    match map.get(&s(key)) {
        Some(Yaml::String(t)) => Some(t),
        _ => None,
    }
}

fn member<'a>(map: &'a Hash, key: &str) -> Result<&'a Yaml, String> {
    map.get(&s(key)).ok_or_else(|| format!("a `{}` type needs `{key}`", text(map, "type").unwrap_or("?")))
}

impl Ty {
    pub(super) fn parse(ty: &Yaml) -> Result<Ty, String> {
        match ty {
            Yaml::String(name) => Ok(match name.as_str() {
                "any" => Ty::Any,
                "integer" => Ty::Int,
                "float" => Ty::Float,
                "string" => Ty::Str,
                "boolean" => Ty::Bool,
                "null" => Ty::Null,
                "list" => Ty::List(Box::new(Ty::Any)),
                "map" => Ty::Map(Box::new(Ty::Any)),
                "secret" => Ty::Secret,
                other => return Err(format!("unknown type `{other}`")),
            }),
            Yaml::Hash(map) => {
                let kind = text(map, "type").ok_or("a type map needs a string `type`")?;
                match kind {
                    "list" => Ok(Ty::List(Box::new(Ty::parse(member(map, "of")?)?))),
                    "map" => Ok(Ty::Map(Box::new(Ty::parse(member(map, "of")?)?))),
                    "union" => match member(map, "of")? {
                        Yaml::Array(members) if !members.is_empty() => Ok(Ty::Union(members.iter().map(Ty::parse).collect::<Result<_, _>>()?)),
                        _ => Err("a `union` needs a non-empty list `of` types".into()),
                    },
                    "enum" => match member(map, "values")? {
                        Yaml::Array(values) if !values.is_empty() => Ok(Ty::Enum(values.clone())),
                        _ => Err("an `enum` needs a non-empty list of `values`".into()),
                    },
                    "literal" => Ok(Ty::Enum(vec![member(map, "value")?.clone()])),
                    "refine" => Ok(Ty::Refine(Box::new(Ty::parse(member(map, "of")?)?), member(map, "check")?.clone())),
                    "record" | "class" => match member(map, "fields")? {
                        Yaml::Hash(fields) => fields.iter().map(|(name, spec)| Ok((key(name)?, Field::parse(spec)?))).collect::<Result<_, String>>().map(Ty::Record),
                        _ => Err(format!("a `{kind}` needs a map of `fields`")),
                    },
                    other => Err(format!("`{other}` is not a type constructor")),
                }
            }
            other => Err(format!("expected a type, got {}", type_name(other))),
        }
    }

    /// Short human description used in violation messages.
    pub(super) fn name(&self) -> String {
        match self {
            Ty::Any => "any".into(),
            Ty::Int => "integer".into(),
            Ty::Float => "float".into(),
            Ty::Str => "string".into(),
            Ty::Bool => "boolean".into(),
            Ty::Null => "null".into(),
            Ty::Secret => "secret".into(),
            Ty::List(of) if **of == Ty::Any => "list".into(),
            Ty::List(of) => format!("list of {}", of.name()),
            Ty::Map(of) if **of == Ty::Any => "map".into(),
            Ty::Map(of) => format!("map of {}", of.name()),
            Ty::Record(_) => "record".into(),
            Ty::Union(members) => members.iter().map(Ty::name).collect::<Vec<_>>().join(" | "),
            Ty::Enum(values) => format!("one of [{}]", values.iter().map(show).collect::<Vec<_>>().join(", ")),
            Ty::Refine(base, _) => format!("refined {}", base.name()),
        }
    }
}

fn key(name: &Yaml) -> Result<String, String> {
    match name {
        Yaml::String(k) => Ok(k.clone()),
        _ => Err("field names must be strings".into()),
    }
}

impl Field {
    /// A field spec: a bare type (required), `[optional, T]` or `[default, T, v]`.
    pub(super) fn parse(spec: &Yaml) -> Result<Field, String> {
        if let Yaml::Hash(map) = spec {
            match text(map, "type") {
                Some("optional") => return Ok(Field { ty: Ty::parse(member(map, "of")?)?, required: false, default: None }),
                Some("default") => return Ok(Field { ty: Ty::parse(member(map, "of")?)?, required: false, default: Some(member(map, "default")?.clone()) }),
                _ => {}
            }
        }
        Ok(Field { ty: Ty::parse(spec)?, required: true, default: None })
    }
}

/// A scalar rendered for messages.
fn show(value: &Yaml) -> String {
    match value {
        Yaml::String(t) | Yaml::Real(t) => t.clone(),
        Yaml::Integer(i) => i.to_string(),
        Yaml::Boolean(b) => b.to_string(),
        Yaml::Null => "null".into(),
        other => type_name(other).into(),
    }
}

fn inside(at: &str, key: &str) -> String {
    if at == "/" { format!("/{key}") } else { format!("{at}/{key}") }
}

fn check(value: &Yaml, ty: &Ty, at: &str, pred: &mut Pred, out: &mut Vec<Violation>) {
    let mismatch = |out: &mut Vec<Violation>| out.push(Violation { at: at.into(), message: format!("expected {}, got {}", ty.name(), type_name(value)) });
    match (ty, value) {
        (Ty::Any, _) | (Ty::Int, Yaml::Integer(_)) | (Ty::Float, Yaml::Real(_)) | (Ty::Str, Yaml::String(_)) | (Ty::Bool, Yaml::Boolean(_)) | (Ty::Null, Yaml::Null) => {}
        (Ty::Secret, Yaml::Hash(h)) if h.len() == 2 && text(h, "type") == Some("secret") && text(h, "ref").is_some() => {}
        (Ty::Secret, Yaml::String(_)) => out.push(Violation { at: at.into(), message: "a secret is a handle (`[secret, ref]`), not an inline string".into() }),
        (Ty::List(of), Yaml::Array(items)) => items.iter().enumerate().for_each(|(i, item)| check(item, of, &inside(at, &i.to_string()), pred, out)),
        (Ty::Map(of), Yaml::Hash(h)) => h.iter().for_each(|(k, v)| check(v, of, &inside(at, &show(k)), pred, out)),
        (Ty::Record(fields), Yaml::Hash(h)) => {
            for (name, field) in fields {
                match h.get(&s(name)) {
                    Some(v) => check(v, &field.ty, &inside(at, name), pred, out),
                    None if field.required => out.push(Violation { at: inside(at, name), message: format!("missing field `{name}`") }),
                    None => {}
                }
            }
            for k in h.keys().filter(|k| !fields.iter().any(|(name, _)| s(name) == **k)) {
                out.push(Violation { at: inside(at, &show(k)), message: format!("unexpected field `{}`", show(k)) });
            }
        }
        (Ty::Union(members), _) if members.iter().any(|m| holds(value, m, pred)) => {}
        (Ty::Enum(values), _) if values.iter().any(|v| equal(v, value)) => {}
        (Ty::Refine(base, predicate), _) => {
            let before = out.len();
            check(value, base, at, pred, out);
            if out.len() == before {
                match pred(predicate, value) {
                    Ok(true) => {}
                    Ok(false) => out.push(Violation { at: at.into(), message: "failed its refinement check".into() }),
                    Err(why) => out.push(Violation { at: at.into(), message: format!("refinement check failed to run: {why}") }),
                }
            }
        }
        _ => mismatch(out),
    }
}

pub(super) fn holds(value: &Yaml, ty: &Ty, pred: &mut Pred) -> bool {
    let mut out = Vec::new();
    check(value, ty, "/", pred, &mut out);
    out.is_empty()
}

/// Every place `value` does not fit the parsed type.
pub(super) fn check_ty(value: &Yaml, ty: &Ty, pred: &mut Pred) -> Vec<Violation> {
    let mut out = Vec::new();
    check(value, ty, "/", pred, &mut out);
    out
}

/// Every place `value` does not fit `ty`; `Err` when `ty` is not a type at all.
pub fn violations(value: &Yaml, ty: &Yaml, pred: &mut Pred) -> Result<Vec<Violation>, String> {
    let mut out = Vec::new();
    check(value, &Ty::parse(ty)?, "/", pred, &mut out);
    Ok(out)
}

/// Whether every value of `a` is a value of `b`. Sound but incomplete: a refinement of `b` is only
/// provable from `a` being that same refinement (or a refinement of it).
pub(super) fn subtype(a: &Ty, b: &Ty, pred: &mut Pred) -> bool {
    if a == b || *b == Ty::Any {
        return true;
    }
    match (a, b) {
        (Ty::Union(members), _) => members.iter().all(|m| subtype(m, b, pred)),
        (_, Ty::Union(members)) => members.iter().any(|m| subtype(a, m, pred)),
        (Ty::Enum(values), _) => values.iter().all(|v| holds(v, b, pred)),
        (Ty::Refine(base, _), _) => subtype(base, b, pred),
        (Ty::List(x), Ty::List(y)) | (Ty::Map(x), Ty::Map(y)) => subtype(x, y, pred),
        (Ty::Record(fa), Ty::Record(fb)) => {
            let fits = |(name, f): &(String, Field)| fb.iter().find(|(n, _)| n == name).is_some_and(|(_, g)| subtype(&f.ty, &g.ty, pred) && (f.required || !g.required));
            fa.iter().all(fits) && fb.iter().filter(|(_, g)| g.required).all(|(n, _)| fa.iter().any(|(m, f)| m == n && f.required))
        }
        _ => false,
    }
}
