//! The type algebra: parsing a type value into `Ty`, checking values against it (collecting every
//! violation), and subtyping. Nothing here converts or repairs a value; it only reports where a
//! value does not fit.

use crate::value::Hash;
use crate::value::Value;

use crate::builtins::value::{equal, is_function, type_name};
use crate::model::string as s;

/// Work only the engine can do for a pure check; its answer is retained and replayed.
pub enum Ask<'a> {
    /// Run a predicate's `{$call: [fn, [value], body]}` source on a value: answers a boolean.
    Predicate(&'a Value, &'a Value),
    /// Evaluate a function's declared parameter and result annotations: answers its signature type.
    /// The flag also asks whether the function is provably free of effects.
    Signature(&'a Value, bool),
}

/// Answers an `Ask`. `Err` is a failure inside the requested work, reported as a violation rather
/// than aborting the check.
pub type Pred<'a> = dyn FnMut(Ask) -> Result<Value, String> + 'a;

/// Whether `predicate` holds for `value`.
pub(super) fn predicate(pred: &mut Pred, predicate: &Value, value: &Value) -> Result<bool, String> {
    match pred(Ask::Predicate(predicate, value))? {
        Value::Boolean(verdict) => Ok(verdict),
        other => Err(format!("returned {}, expected boolean", type_name(&other))),
    }
}

/// Human-readable label only; semantic origin is carried separately by `ValueKind`.
pub(super) const DEFAULT_SOURCE: &str = "default of ";

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(super) enum ValueKind {
    Explicit,
    Default,
}

impl ValueKind {
    pub(super) fn as_str(self) -> &'static str {
        match self {
            Self::Explicit => "explicit",
            Self::Default => "default",
        }
    }
}

/// Names and display labels cannot distinguish defaults from explicitly fixed values.
/// Legacy descriptors are ambiguous, so require the semantic discriminator rather than guessing.
pub(super) fn value_kind(class: &Hash, field: &str) -> Result<ValueKind, String> {
    let kind = match class.get(&s("provenance")) {
        Some(Value::Hash(provenance)) => match provenance.get(&s(field)) {
            Some(Value::Hash(origin)) => text(origin, "value_kind"),
            _ => None,
        },
        _ => None,
    };
    match kind {
        Some("explicit") => Ok(ValueKind::Explicit),
        Some("default") => Ok(ValueKind::Default),
        _ => Err(format!("class value `{field}` requires provenance `value_kind` of `explicit` or `default`; rebuild the class from its declaration")),
    }
}

const PRIMITIVES: [&str; 12] = ["any", "never", "integer", "float", "string", "boolean", "null", "list", "map", "secret", "function", "vm"];

/// Whether `name` is a primitive type, which evaluates as a bare symbol when nothing shadows it.
pub fn is_primitive(name: &str) -> bool {
    PRIMITIVES.contains(&name)
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Ty {
    Any,
    Never,
    Int,
    Float,
    Str,
    Bool,
    Null,
    Secret,
    /// Any function: the top of the function types, and all that `function` says.
    Function,
    /// A function declared to take these parameters and return this result; `true` also requires
    /// it to be provably free of effects.
    Fn(Vec<Ty>, Box<Ty>, bool),
    Vm,
    List(Box<Ty>),
    Map(Box<Ty>),
    /// Closed: a value may not carry fields the record does not declare.
    Record(Vec<(String, Field)>),
    /// A closed record that also pins the fixed values a class set explicitly and must satisfy its
    /// whole-record constraints; defaults filled in by realization are not pinned.
    Class(Vec<(String, Field)>, Vec<(String, Value)>, Vec<Value>),
    Union(Vec<Ty>),
    Enum(Vec<Value>),
    /// A base type plus the raw source of a predicate.
    Refine(Box<Ty>, Value),
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct Field {
    pub ty: Ty,
    pub required: bool,
    pub default: Option<Value>,
}

/// A place where a value does not fit its type: `at` is the path inside the checked value.
pub struct Violation {
    at: String,
    message: String,
}

pub(super) fn mapping<const N: usize>(entries: [(&str, Value); N]) -> Value {
    Value::Hash(entries.into_iter().map(|(k, v)| (s(k), v)).collect())
}

impl Violation {
    pub fn at(&self) -> &str {
        &self.at
    }

    pub fn message(&self) -> &str {
        &self.message
    }

    pub fn to_value(&self) -> Value {
        mapping([("at", s(&self.at)), ("message", s(&self.message))])
    }
}


pub(super) fn text<'a>(map: &'a Hash, key: &str) -> Option<&'a str> {
    match map.get(&s(key)) {
        Some(Value::String(t)) => Some(t),
        _ => None,
    }
}

fn member<'a>(map: &'a Hash, key: &str) -> Result<&'a Value, String> {
    map.get(&s(key)).ok_or_else(|| format!("a `{}` type needs `{key}`", text(map, "type").unwrap_or("?")))
}

impl Ty {
    pub(crate) fn parse(ty: &Value) -> Result<Ty, String> {
        match ty {
            Value::String(name) => Ok(match name.as_str() {
                "any" => Ty::Any,
                "never" => Ty::Never,
                "integer" => Ty::Int,
                "float" => Ty::Float,
                "string" => Ty::Str,
                "boolean" => Ty::Bool,
                "null" => Ty::Null,
                "list" => Ty::List(Box::new(Ty::Any)),
                "map" => Ty::Map(Box::new(Ty::Any)),
                "secret" => Ty::Secret,
                "function" => Ty::Function,
                "vm" => Ty::Vm,
                other => return Err(format!("unknown type `{other}`")),
            }),
            Value::Hash(map) => {
                let kind = text(map, "type").ok_or("a type map needs a string `type`")?;
                match kind {
                    "list" => Ok(Ty::List(Box::new(Ty::parse(member(map, "of")?)?))),
                    "map" => Ok(Ty::Map(Box::new(Ty::parse(member(map, "of")?)?))),
                    "union" => match member(map, "of")? {
                        Value::Array(members) if !members.is_empty() => Ok(Ty::Union(members.iter().map(Ty::parse).collect::<Result<_, _>>()?)),
                        _ => Err("a `union` needs a non-empty list `of` types".into()),
                    },
                    "enum" => match member(map, "values")? {
                        Value::Array(values) if !values.is_empty() => Ok(Ty::Enum(values.to_vec())),
                        _ => Err("an `enum` needs a non-empty list of `values`".into()),
                    },
                    "literal" => Ok(Ty::Enum(vec![member(map, "value")?.clone()])),
                    "function" => {
                        let Value::Array(params) = member(map, "params")? else { return Err("a `function` type needs a list of `params` types".into()) };
                        let pure = match map.get(&s("pure")) {
                            None => false,
                            Some(Value::Boolean(pure)) => *pure,
                            Some(_) => return Err("a `function` type's `pure` must be a boolean".into()),
                        };
                        Ok(Ty::Fn(params.iter().map(Ty::parse).collect::<Result<_, _>>()?, Box::new(Ty::parse(member(map, "result")?)?), pure))
                    }
                    "refine" => {
                        let base = Ty::parse(member(map, "of")?)?;
                        let predicate = member(map, "check")?;
                        if !is_function(predicate) {
                            return Err("a `refine` type needs a function `check`".into());
                        }
                        Ok(Ty::Refine(Box::new(base), predicate.clone()))
                    }
                    "record" | "class" => {
                        let Value::Hash(fields) = member(map, "fields")? else { return Err(format!("a `{kind}` needs a map of `fields`")) };
                        let fields = fields.iter().map(|(name, spec)| Ok((key(name)?, Field::parse(spec)?))).collect::<Result<Vec<_>, String>>()?;
                        if kind == "record" {
                            return Ok(Ty::Record(fields));
                        }
                        Ok(Ty::Class(fields, fixed_values(map)?, constraints(map)?))
                    }
                    other => Err(format!("`{other}` is not a type constructor")),
                }
            }
            other => Err(format!("expected a type, got {}", type_name(other))),
        }
    }

    /// Whether every value of `self` is provably a value of `other`; predicates are never run.
    pub(crate) fn below(&self, other: &Ty) -> bool {
        subtype(self, other, &mut |_| Err("static".into()))
    }

    /// Whether some value could inhabit both: `false` only when the types are clearly disjoint.
    /// Refinements are taken at their base, records and element types are not compared deeply.
    pub(crate) fn overlaps(&self, other: &Ty) -> bool {
        match (self, other) {
            (Ty::Never, _) | (_, Ty::Never) => false,
            (Ty::Any, _) | (_, Ty::Any) => true,
            (Ty::Union(members), _) => members.iter().any(|m| m.overlaps(other)),
            (_, Ty::Union(members)) => members.iter().any(|m| self.overlaps(m)),
            (Ty::Refine(base, _), _) => base.overlaps(other),
            (_, Ty::Refine(base, _)) => self.overlaps(base),
            (Ty::Enum(values), _) => values.iter().any(|v| super::literal::may_overlap(v, other)),
            (_, Ty::Enum(values)) => values.iter().any(|v| super::literal::may_overlap(v, self)),
            (Ty::List(_), Ty::List(_)) | (Ty::Map(_), Ty::Map(_)) => true,
            (Ty::Map(_), Ty::Record(_) | Ty::Class(..)) | (Ty::Record(_) | Ty::Class(..), Ty::Map(_)) => true,
            (Ty::Record(_) | Ty::Class(..), Ty::Record(_) | Ty::Class(..)) => true,
            (Ty::Function | Ty::Fn(..), Ty::Function | Ty::Fn(..)) => true,
            (Ty::Int, Ty::Int) | (Ty::Float, Ty::Float) | (Ty::Str, Ty::Str) | (Ty::Bool, Ty::Bool) | (Ty::Null, Ty::Null) | (Ty::Secret, Ty::Secret) | (Ty::Vm, Ty::Vm) => true,
            _ => false,
        }
    }

    /// This function type with the purity requirement met, so a failed check can say whether purity
    /// alone was the difference.
    fn purely(&self) -> Ty {
        match self {
            Ty::Fn(params, result, _) => Ty::Fn(params.clone(), result.clone(), true),
            other => other.clone(),
        }
    }

    /// Short human description used in violation messages.
    pub(crate) fn name(&self) -> String {
        match self {
            Ty::Any => "any".into(),
            Ty::Never => "never".into(),
            Ty::Int => "integer".into(),
            Ty::Float => "float".into(),
            Ty::Str => "string".into(),
            Ty::Bool => "boolean".into(),
            Ty::Null => "null".into(),
            Ty::Secret => "secret".into(),
            Ty::Function => "function".into(),
            Ty::Fn(params, result, pure) => format!("{}function ({}) -> {}", if *pure { "pure " } else { "" }, params.iter().map(Ty::name).collect::<Vec<_>>().join(", "), result.name()),
            Ty::Vm => "vm".into(),
            Ty::List(of) if **of == Ty::Any => "list".into(),
            Ty::List(of) => format!("list of {}", of.name()),
            Ty::Map(of) if **of == Ty::Any => "map".into(),
            Ty::Map(of) => format!("map of {}", of.name()),
            Ty::Record(_) => "record".into(),
            Ty::Class(..) => "class".into(),
            Ty::Union(members) => members.iter().map(Ty::name).collect::<Vec<_>>().join(" | "),
            Ty::Enum(values) => format!("one of [{}]", values.iter().map(show).collect::<Vec<_>>().join(", ")),
            Ty::Refine(base, _) => format!("refined {}", base.name()),
        }
    }
}

/// The values a class pins: those set explicitly rather than filled from a default.
fn fixed_values(class: &Hash) -> Result<Vec<(String, Value)>, String> {
    let Some(Value::Hash(values)) = class.get(&s("values")) else { return Ok(Vec::new()) };
    let mut fixed = Vec::new();
    for (name, value) in values {
        let name = key(name)?;
        if value_kind(class, &name)? == ValueKind::Explicit {
            fixed.push((name, value.clone()));
        }
    }
    Ok(fixed)
}

/// Validate declaration shape independently of whether a value reaches its predicates.
pub(super) fn constraints(class: &Hash) -> Result<Vec<Value>, String> {
    match class.get(&s("constraints")) {
        None => Ok(Vec::new()),
        Some(Value::Array(items)) => items.iter().enumerate().map(|(index, item)| {
            if is_function(item) {
                Ok(item.clone())
            } else {
                Err(format!("class constraint {index} must be a function"))
            }
        }).collect(),
        Some(_) => Err("class `constraints` must be a list of functions".into()),
    }
}

fn key(name: &Value) -> Result<String, String> {
    match name {
        Value::String(k) => Ok(k.clone()),
        _ => Err("field names must be strings".into()),
    }
}

impl Field {
    /// A field spec: a bare type (required), `[optional, T]` or `[default, T, v]`.
    pub(super) fn parse(spec: &Value) -> Result<Field, String> {
        if let Value::Hash(map) = spec {
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
fn show(value: &Value) -> String {
    match value {
        Value::String(t) => t.clone(),
        Value::Float(t) => format!("{t:?}"),
        Value::Integer(i) => i.to_string(),
        Value::Boolean(b) => b.to_string(),
        Value::Null => "null".into(),
        other => type_name(other).into(),
    }
}

fn inside(at: &str, key: &str) -> String {
    if at == "/" { format!("/{key}") } else { format!("{at}/{key}") }
}

fn check(value: &Value, ty: &Ty, at: &str, pred: &mut Pred, out: &mut Vec<Violation>) {
    let mismatch = |out: &mut Vec<Violation>| out.push(Violation { at: at.into(), message: format!("expected {}, got {}", ty.name(), type_name(value)) });
    match (ty, value) {
        (Ty::Any, _) | (Ty::Int, Value::Integer(_)) | (Ty::Float, Value::Float(_)) | (Ty::Str, Value::String(_)) | (Ty::Bool, Value::Boolean(_)) | (Ty::Null, Value::Null) => {}
        (Ty::Function, _) if is_function(value) => {}
        (Ty::Fn(..), _) if is_function(value) => match pred(Ask::Signature(value, matches!(ty, Ty::Fn(.., true)))).and_then(|declared| Ty::parse(&declared)) {
            Ok(declared) if subtype(&declared, ty, pred) => {}
            Ok(declared) => {
                let unproven = matches!((&declared, ty), (Ty::Fn(.., false), Ty::Fn(.., true))) && subtype(&declared.purely(), ty, pred);
                let note = if unproven { " (not provably pure)" } else { "" };
                out.push(Violation { at: at.into(), message: format!("expected {}, got {}{note}", ty.name(), declared.name()) })
            }
            Err(why) => out.push(Violation { at: at.into(), message: format!("signature check failed to run: {why}") }),
        },
        (Ty::Vm, Value::Machine(_)) => {}
        (Ty::Secret, Value::Hash(h)) if h.len() == 2 && text(h, "type") == Some("secret") && text(h, "ref").is_some() => {}
        (Ty::Secret, Value::String(_)) => out.push(Violation { at: at.into(), message: "a secret is a handle (`[secret, ref]`), not an inline string".into() }),
        (Ty::List(of), Value::Array(items)) => items.iter().enumerate().for_each(|(i, item)| check(item, of, &inside(at, &i.to_string()), pred, out)),
        (Ty::Map(of), Value::Hash(h)) => h.iter().for_each(|(k, v)| check(v, of, &inside(at, &show(k)), pred, out)),
        (Ty::Record(fields), Value::Hash(h)) => check_record(fields, h, at, pred, out),
        (Ty::Class(fields, fixed, constraints), Value::Hash(h)) => {
            let before = out.len();
            check_record(fields, h, at, pred, out);
            for (name, pinned) in fixed {
                let (here, reported) = (inside(at, name), out.iter().any(|v| v.at == inside(at, name)));
                if !reported && !h.get(&s(name)).is_some_and(|v| equal(v, pinned)) {
                    out.push(Violation { at: here, message: format!("field `{name}` must equal {}", show(pinned)) });
                }
            }
            if out.len() == before {
                for (i, constraint) in constraints.iter().enumerate() {
                    match predicate(pred, constraint, value) {
                        Ok(true) => {}
                        Ok(false) => out.push(Violation { at: at.into(), message: format!("constraint {i} does not hold") }),
                        Err(why) => out.push(Violation { at: at.into(), message: format!("constraint {i} failed to run: {why}") }),
                    }
                }
            }
        }
        (Ty::Union(members), _) if members.iter().any(|m| holds(value, m, pred)) => {}
        (Ty::Enum(values), _) if values.iter().any(|v| equal(v, value)) => {}
        (Ty::Refine(base, check_fn), _) => {
            let before = out.len();
            check(value, base, at, pred, out);
            if out.len() == before {
                match predicate(pred, check_fn, value) {
                    Ok(true) => {}
                    Ok(false) => out.push(Violation { at: at.into(), message: "failed its refinement check".into() }),
                    Err(why) => out.push(Violation { at: at.into(), message: format!("refinement check failed to run: {why}") }),
                }
            }
        }
        _ => mismatch(out),
    }
}

fn check_record(fields: &[(String, Field)], h: &Hash, at: &str, pred: &mut Pred, out: &mut Vec<Violation>) {
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

pub(super) fn holds(value: &Value, ty: &Ty, pred: &mut Pred) -> bool {
    let mut out = Vec::new();
    check(value, ty, "/", pred, &mut out);
    out.is_empty()
}

/// Every place `value` does not fit the parsed type.
pub(super) fn check_ty(value: &Value, ty: &Ty, pred: &mut Pred) -> Vec<Violation> {
    let mut out = Vec::new();
    check(value, ty, "/", pred, &mut out);
    out
}

/// Every place `value` does not fit `ty`; `Err` when `ty` is not a type at all.
pub fn violations(value: &Value, ty: &Value, pred: &mut Pred) -> Result<Vec<Violation>, String> {
    let mut out = Vec::new();
    check(value, &Ty::parse(ty)?, "/", pred, &mut out);
    Ok(out)
}

/// Whether every value of `a` is a value of `b`. Sound but incomplete: a refinement of `b` is only
/// provable from `a` being that same refinement (or a refinement of it).
pub(super) fn subtype(a: &Ty, b: &Ty, pred: &mut Pred) -> bool {
    if a == b || *a == Ty::Never || *b == Ty::Any {
        return true;
    }
    match (a, b) {
        (Ty::Union(members), _) => members.iter().all(|m| subtype(m, b, pred)),
        (Ty::Enum(values), _) => values.iter().all(|v| super::literal::covered_by(v, b, pred)),
        (_, Ty::Union(members)) => members.iter().any(|m| subtype(a, m, pred)),
        (Ty::Refine(base, _), _) => subtype(base, b, pred),
        (Ty::Fn(..), Ty::Function) => true,
        // Parameters are contravariant, the result covariant: a function that accepts more and
        // promises more can stand wherever the narrower signature is expected.
        // A pure function stands wherever an unconstrained one is expected, never the reverse.
        (Ty::Fn(pa, ra, ea), Ty::Fn(pb, rb, eb)) => (*ea || !*eb) && pa.len() == pb.len() && pa.iter().zip(pb).all(|(x, y)| subtype(y, x, pred)) && subtype(ra, rb, pred),
        (Ty::List(x), Ty::List(y)) | (Ty::Map(x), Ty::Map(y)) => subtype(x, y, pred),
        (Ty::Record(fa) | Ty::Class(fa, ..), Ty::Record(fb)) => record_subtype(fa, fb, pred),
        (Ty::Class(fa, xa, ca), Ty::Class(fb, xb, cb)) => {
            record_subtype(fa, fb, pred) && xb.iter().all(|(n, v)| xa.iter().any(|(m, w)| m == n && equal(v, w))) && cb.iter().all(|c| ca.contains(c))
        }
        _ => false,
    }
}

pub(super) fn record_subtype(fa: &[(String, Field)], fb: &[(String, Field)], pred: &mut Pred) -> bool {
    let fits = |(name, f): &(String, Field)| fb.iter().find(|(n, _)| n == name).is_some_and(|(_, g)| subtype(&f.ty, &g.ty, pred) && (f.required || !g.required));
    fa.iter().all(fits) && fb.iter().filter(|(_, g)| g.required).all(|(n, _)| fa.iter().any(|(m, f)| m == n && f.required))
}
