//! Type classes: incomplete descriptions of a record that combine by inheritance. A class is
//! `{type: class, name, parents, fields, values, provenance, concrete}`. Inheriting merges fields,
//! defaults and fixed values and nothing else (no methods, no dispatch), so the result is always
//! the flat record one could have written by hand.
//!
//! Merge rules, in full:
//! - parents contribute in order; a field's declaration and its fixed value each come from one source;
//! - two parents that disagree on a field's declaration, or on its fixed value, are a `conflict`
//!   unless the inheriting class resolves it: it may redeclare the field (as a subtype of every
//!   parent's declaration) and set the value;
//! - lists and maps never merge element-wise: a value is taken whole or conflicts;
//! - `realize` fills defaults and refuses (`abstract`) while a required field has no value.

use crate::value::Hash;
use crate::value::Value;

use super::construct::{descriptor, reflect};
use super::ty::{check_ty, constraints, mapping, predicate, subtype, value_kind, Field, Pred, Ty, ValueKind, DEFAULT_SOURCE};
use crate::builtins::fault::Fault;
use crate::builtins::value::{equal, is_function};
use crate::model::string as s;

/// One field of a class: its declaration, who declared it, and its value with who supplied it.
#[derive(Clone)]
struct Slot {
    name: String,
    spec: Value,
    declared_by: String,
    value: Option<(Value, String, ValueKind)>,
}

/// Argument positions of the declared fields and fixed values, for blaming the right one.
#[derive(Clone, Copy)]
struct Where {
    fields: usize,
    values: usize,
}

struct Class {
    name: String,
    parents: Vec<String>,
    slots: Vec<Slot>,
    /// Raw predicates over the whole record of values, checked when the class is realized.
    constraints: Vec<Value>,
    concrete: bool,
}

pub(super) fn apply(actor: &str, args: &[Value], pred: &mut Pred) -> Result<Value, Fault> {
    match actor {
        "class" => {
            let values = if args.len() > 2 { hash_arg(args, 2)? } else { &Hash::new() };
            Ok(extend(name_arg(args, 0)?, &[], fields_arg(args, 1)?, values, Where { fields: 1, values: 2 }, pred)?.to_value())
        }
        "extend" => Ok(extend(name_arg(args, 0)?, &parents_arg(args, 1)?, fields_arg(args, 2)?, hash_arg(args, 3)?, Where { fields: 2, values: 3 }, pred)?.to_value()),
        "inherit" => {
            let class = extend(name_arg(args, 0)?, &parents_arg(args, 1)?, &Hash::new(), hash_arg(args, 2)?, Where { fields: 1, values: 2 }, pred)?;
            Ok(realize(class, None, pred)?.to_value())
        }
        "realize" => Ok(realize(Class::from_value(&args[0], 0)?, Some(0), pred)?.to_value()),
        "parents" => reflect_parents(&args[0]),
        "open_fields" => open_fields(&args[0]),
        "constrain" => {
            let mut class = Class::from_value(&args[0], 0)?;
            if !is_function(&args[1]) {
                return Err(Fault::expected(1, "function", &args[1]));
            }
            class.constraints.push(args[1].clone());
            // A stronger declaration needs explicit realization before its values are planned.
            class.concrete = false;
            Ok(class.to_value())
        }
        _ => unreachable!("`{actor}` is not a class builtin"),
    }
}

/// `[parents, class]`: the names of the classes it was built from.
fn reflect_parents(class: &Value) -> Result<Value, Fault> {
    reflect(class, "parents")
}

/// Required fields still needing a value; defaults and optional fields need no override.
fn open_fields(value: &Value) -> Result<Value, Fault> {
    let class = Class::from_value(value, 0)?;
    let mut fields = Hash::new();
    for slot in class.slots {
        let field = Field::parse(&slot.spec).map_err(|why| Fault::at(0, "type", why))?;
        if slot.value.is_none() && field.required && field.default.is_none() {
            fields.insert(s(&slot.name), slot.spec);
        }
    }
    Ok(Value::Hash(fields))
}

fn name_arg(args: &[Value], at: usize) -> Result<&str, Fault> {
    match &args[at] {
        Value::String(name) if !name.is_empty() => Ok(name),
        other => Err(Fault::expected(at, "a non-empty class name", other)),
    }
}

fn hash_arg(args: &[Value], at: usize) -> Result<&Hash, Fault> {
    match &args[at] {
        Value::Hash(h) => Ok(h),
        other => Err(Fault::expected(at, "map", other)),
    }
}

/// The declared fields of the record type at `at`.
fn fields_arg(args: &[Value], at: usize) -> Result<&Hash, Fault> {
    match &args[at] {
        Value::Hash(map) if map.get(&s("type")) == Some(&s("record")) => match map.get(&s("fields")) {
            Some(Value::Hash(fields)) => Ok(fields),
            _ => Err(Fault::at(at, "type", "malformed record type".into())),
        },
        other => Err(Fault::expected(at, "a record type of fields", other)),
    }
}

fn parents_arg(args: &[Value], at: usize) -> Result<Vec<Class>, Fault> {
    match &args[at] {
        Value::Array(items) => items.iter().map(|item| Class::from_value(item, at)).collect(),
        other => Err(Fault::expected(at, "list of classes", other)),
    }
}

impl Class {
    fn from_value(class: &Value, arg: usize) -> Result<Class, Fault> {
        let bad = || Fault::at(arg, "type", "expected a class".into());
        let Value::Hash(map) = class else { return Err(bad()) };
        let get = |k: &str| map.get(&s(k));
        let (Some(Value::String(name)), Some(Value::Hash(fields)), Some(Value::Hash(values)), Some(Value::Hash(prov)), Some(Value::Array(parents)), Some(Value::Boolean(concrete))) =
            (get("name"), get("fields"), get("values"), get("provenance"), get("parents"), get("concrete"))
        else {
            return Err(bad());
        };
        if get("type") != Some(&s("class")) {
            return Err(bad());
        }
        let source = |field: &str, which: &str| match prov.get(&s(field)) {
            Some(Value::Hash(entry)) => match entry.get(&s(which)) {
                Some(Value::String(by)) => Ok(by.clone()),
                _ => Err(bad()),
            },
            _ => Err(bad()),
        };
        let constraints = constraints(map).map_err(|why| Fault::at(arg, "type", why))?;
        let slots = fields
            .iter()
            .map(|(field, spec)| {
                let Value::String(field) = field else { return Err(bad()) };
                let value = values.get(&s(field)).map(|v| {
                    let kind = value_kind(map, field).map_err(|why| Fault::at(arg, "type", why))?;
                    Ok((v.clone(), source(field, "value")?, kind))
                }).transpose()?;
                Ok(Slot { name: field.clone(), spec: spec.clone(), declared_by: source(field, "type")?, value })
            })
            .collect::<Result<_, _>>()?;
        let parents = parents.iter().map(|p| if let Value::String(p) = p { Ok(p.clone()) } else { Err(bad()) }).collect::<Result<_, _>>()?;
        Ok(Class { name: name.clone(), parents, slots, constraints, concrete: *concrete })
    }

    fn to_value(&self) -> Value {
        let (mut fields, mut values, mut provenance) = (Hash::new(), Hash::new(), Hash::new());
        for slot in &self.slots {
            fields.insert(s(&slot.name), slot.spec.clone());
            let mut origin = Hash::new();
            origin.insert(s("type"), s(&slot.declared_by));
            if let Some((value, by, kind)) = &slot.value {
                values.insert(s(&slot.name), value.clone());
                origin.insert(s("value"), s(by));
                origin.insert(s("value_kind"), s(kind.as_str()));
            }
            provenance.insert(s(&slot.name), Value::Hash(origin));
        }
        let mut class = descriptor("class", [("name", s(&self.name)), ("parents", Value::Array(self.parents.iter().map(|p| s(p)).collect()))]);
        if let Value::Hash(map) = &mut class {
            for (k, v) in [("fields", fields), ("values", values)] {
                map.insert(s(k), Value::Hash(v));
            }
            if !self.constraints.is_empty() {
                map.insert(s("constraints"), Value::Array(self.constraints.clone().into()));
            }
            map.insert(s("provenance"), Value::Hash(provenance));
            map.insert(s("concrete"), Value::Boolean(self.concrete));
        }
        class
    }
}

fn conflict(field: &str, first: &str, second: &str, what: &str, resolver: &str) -> Fault {
    let message = format!("field `{field}` has {what} from both `{first}` and `{second}`; resolve it in `{resolver}`");
    let data = mapping([("field", s(field)), ("sources", Value::Array(vec![s(first), s(second)].into()))]);
    Fault { cause: None, kind: "conflict".into(), message, arg: Some(1), data: Some(data) }
}

/// Merge `parents` with this class's own `fields` and `values` (arguments 2 and 3 of `extend`).
fn extend(name: &str, parents: &[Class], fields: &Hash, values: &Hash, at: Where, pred: &mut Pred) -> Result<Class, Fault> {
    let mut order: Vec<&str> = Vec::new();
    for slot in parents.iter().flat_map(|p| &p.slots) {
        if !order.contains(&slot.name.as_str()) {
            order.push(&slot.name);
        }
    }
    let own_order: Vec<String> = fields.keys().map(|k| if let Value::String(k) = k { k.clone() } else { String::new() }).collect();
    let mut slots = Vec::new();
    for field in order.iter().map(|f| f.to_string()).chain(own_order.into_iter().filter(|f| !parents.iter().any(|p| p.slots.iter().any(|s| s.name == *f)))) {
        let inherited: Vec<&Slot> = parents.iter().filter_map(|p| p.slots.iter().find(|s| s.name == field)).collect();
        let own_spec = fields.get(&s(&field));
        let own_value = values.get(&s(&field));
        let (spec, declared_by) = match own_spec {
            Some(spec) => {
                narrow(&field, spec, &inherited, name, at.fields, pred)?;
                (spec.clone(), name.to_string())
            }
            None => match inherited.iter().find(|other| !equal(&other.spec, &inherited[0].spec)) {
                Some(other) => return Err(conflict(&field, &inherited[0].declared_by, &other.declared_by, "different declarations", name)),
                None => (inherited[0].spec.clone(), inherited[0].declared_by.clone()),
            },
        };
        let value = match own_value {
            Some(v) => Some((v.clone(), name.to_string(), ValueKind::Explicit)),
            None => {
                let supplied: Vec<&(Value, String, ValueKind)> = inherited.iter().filter_map(|s| s.value.as_ref()).collect();
                match supplied.iter().find(|other| !equal(&other.0, &supplied[0].0)) {
                    Some(other) => return Err(conflict(&field, &supplied[0].1, &other.1, "different values", name)),
                    // An equal default cannot erase another parent's fixed-value obligation.
                    None => supplied.iter().find(|v| v.2 == ValueKind::Explicit)
                        .or_else(|| supplied.first()).map(|v| (*v).clone()),
                }
            }
        };
        slots.push(Slot { name: field, spec, declared_by, value });
    }
    if let Some(stray) = values.keys().find(|k| !slots.iter().any(|slot| s(&slot.name) == **k)) {
        let Value::String(stray) = stray else { unreachable!("value names are strings") };
        return Err(Fault::at(at.values, "type", format!("`{name}` sets `{stray}`, which no parent or field declares")));
    }
    let mut constraints: Vec<Value> = Vec::new();
    for constraint in parents.iter().flat_map(|p| &p.constraints) {
        if !constraints.contains(constraint) {
            constraints.push(constraint.clone());
        }
    }
    let class = Class { name: name.to_string(), parents: parents.iter().map(|p| p.name.clone()).collect(), slots, constraints, concrete: false };
    validate(&class, pred)?;
    Ok(class)
}

/// A redeclared field must fit every inherited declaration (be a subtype, and no less required).
fn narrow(field: &str, spec: &Value, inherited: &[&Slot], name: &str, arg: usize, pred: &mut Pred) -> Result<(), Fault> {
    let own = Field::parse(spec).map_err(|why| Fault::at(arg, "type", why))?;
    for slot in inherited {
        let theirs = Field::parse(&slot.spec).map_err(|why| Fault::at(1, "type", why))?;
        if !subtype(&own.ty, &theirs.ty, pred) || (!own.required && theirs.required) {
            let message = format!("`{name}` redeclares field `{field}` in a way that does not fit the declaration from `{}`", slot.declared_by);
            let data = mapping([("field", s(field)), ("sources", Value::Array(vec![s(&slot.declared_by), s(name)].into()))]);
            return Err(Fault { cause: None, kind: "conflict".into(), message, arg: Some(arg), data: Some(data) });
        }
    }
    Ok(())
}

/// Every set value must fit its field's type; all misfits are reported together.
fn validate(class: &Class, pred: &mut Pred) -> Result<(), Fault> {
    let mut found = Vec::new();
    for slot in &class.slots {
        let Some((value, _, _)) = &slot.value else { continue };
        let ty = Field::parse(&slot.spec).map_err(|why| Fault::at(2, "type", why))?.ty;
        found.extend(check_slot(&slot.name, value, &ty, pred));
    }
    if found.is_empty() {
        return Ok(());
    }
    let message = format!("{} misfit{} in the values of `{}`: {}", found.len(), if found.len() == 1 { "" } else { "s" }, class.name, found[0].1);
    Err(Fault { cause: None, kind: "type".into(), message, arg: None, data: Some(mapping([("violations", Value::Array(found.into_iter().map(|f| f.0).collect()))])) })
}

fn check_slot(field: &str, value: &Value, ty: &Ty, pred: &mut Pred) -> Vec<(Value, String)> {
    check_ty(value, ty, pred)
        .into_iter()
        .map(|v| {
            let at = format!("/{field}{}", if v.at() == "/" { String::new() } else { v.at().to_string() });
            (mapping([("at", s(&at)), ("message", s(v.message()))]), format!("{at}: {}", v.message()))
        })
        .collect()
}

/// Whole-record constraints run on the realized values; each failure is reported.
fn constrained(class: &Class, pred: &mut Pred) -> Result<(), Fault> {
    let values = Value::Hash(class.slots.iter().filter_map(|slot| slot.value.as_ref().map(|(v, _, _)| (s(&slot.name), v.clone()))).collect());
    let mut found = Vec::new();
    for (i, check) in class.constraints.iter().enumerate() {
        let why = match predicate(pred, check, &values) {
            Ok(true) => continue,
            Ok(false) => format!("constraint {i} of `{}` does not hold", class.name),
            Err(why) => format!("constraint {i} of `{}` failed to run: {why}", class.name),
        };
        found.push(why);
    }
    if found.is_empty() {
        return Ok(());
    }
    let entries:Vec<_> = found.iter().map(|why| mapping([("at", s("/")), ("message", s(why))])).collect();
    Err(Fault { cause: None, kind: "type".into(), message: found[0].clone(), arg: None, data: Some(mapping([("violations", Value::Array(entries.into()))])) })
}

/// Fill defaults and mark the class concrete, or fail listing every required field still open.
fn realize(mut class: Class, arg: Option<usize>, pred: &mut Pred) -> Result<Class, Fault> {
    let mut missing = Vec::new();
    for slot in &mut class.slots {
        if slot.value.is_some() {
            continue;
        }
        let field = Field::parse(&slot.spec).map_err(|why| Fault { arg, ..Fault::whole("type", why) })?;
        match field.default {
            Some(default) => slot.value = Some((default, format!("{DEFAULT_SOURCE}{}", slot.declared_by), ValueKind::Default)),
            None if field.required => missing.push(slot.clone()),
            None => {}
        }
    }
    if !missing.is_empty() {
        let list = missing.iter().map(|m| format!("`{}` (required by {})", m.name, m.declared_by)).collect::<Vec<_>>().join(", ");
        let entries:Vec<_> = missing.iter().map(|m| mapping([("field", s(&m.name)), ("required_by", s(&m.declared_by))])).collect();
        let message = format!("class `{}` is abstract: missing {list}", class.name);
        return Err(Fault { cause: None, kind: "abstract".into(), message, arg, data: Some(mapping([("missing", Value::Array(entries.into()))])) });
    }
    validate(&class, pred)?;
    constrained(&class, pred)?;
    class.concrete = true;
    Ok(class)
}

/// The values of a realized class, for machine-level builtins.
pub(super) fn concrete_values(class: &Value, arg: usize) -> Result<Hash, Fault> {
    let parsed = Class::from_value(class, arg)?;
    if !parsed.concrete {
        return Err(Fault::at(arg, "abstract", format!("class `{}` is abstract; realize it first", parsed.name)));
    }
    Ok(parsed.slots.into_iter().filter_map(|slot| slot.value.map(|(v, _, _)| (s(&slot.name), v))).collect())
}
