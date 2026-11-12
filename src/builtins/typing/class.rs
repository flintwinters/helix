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

use yaml_rust2::yaml::Hash;
use yaml_rust2::Yaml;

use super::construct::{descriptor, reflect};
use super::ty::{check_ty, mapping, subtype, Field, Pred, Ty};
use crate::builtins::fault::Fault;
use crate::value::equal;

/// One field of a class: its declaration, who declared it, and its value with who supplied it.
#[derive(Clone)]
struct Slot {
    name: String,
    spec: Yaml,
    declared_by: String,
    value: Option<(Yaml, String)>,
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
    constraints: Vec<Yaml>,
    concrete: bool,
}

pub(super) fn apply(actor: &str, args: &[Yaml], pred: &mut Pred) -> Result<Yaml, Fault> {
    match actor {
        "class" => {
            let values = if args.len() > 2 { hash_arg(args, 2)? } else { &Hash::new() };
            Ok(extend(name_arg(args, 0)?, &[], fields_arg(args, 1)?, values, Where { fields: 1, values: 2 }, pred)?.to_yaml())
        }
        "extend" => Ok(extend(name_arg(args, 0)?, &parents_arg(args, 1)?, fields_arg(args, 2)?, hash_arg(args, 3)?, Where { fields: 2, values: 3 }, pred)?.to_yaml()),
        "inherit" => {
            let class = extend(name_arg(args, 0)?, &parents_arg(args, 1)?, &Hash::new(), hash_arg(args, 2)?, Where { fields: 1, values: 2 }, pred)?;
            Ok(realize(class, None, pred)?.to_yaml())
        }
        "realize" => Ok(realize(Class::from_yaml(&args[0], 0)?, Some(0), pred)?.to_yaml()),
        "parents" => reflect_parents(&args[0]),
        "open_fields" => open_fields(&args[0]),
        "constrain" => {
            let mut class = Class::from_yaml(&args[0], 0)?;
            class.constraints.push(args[1].clone());
            Ok(class.to_yaml())
        }
        _ => unreachable!("`{actor}` is not a class builtin"),
    }
}

/// `[parents, class]`: the names of the classes it was built from.
fn reflect_parents(class: &Yaml) -> Result<Yaml, Fault> {
    reflect(class, "parents")
}

/// Required fields still needing a value; defaults and optional fields need no override.
fn open_fields(value: &Yaml) -> Result<Yaml, Fault> {
    let class = Class::from_yaml(value, 0)?;
    let mut fields = Hash::new();
    for slot in class.slots {
        let field = Field::parse(&slot.spec).map_err(|why| Fault::at(0, "type", why))?;
        if slot.value.is_none() && field.required && field.default.is_none() {
            fields.insert(Yaml::String(slot.name.to_string()), slot.spec);
        }
    }
    Ok(Yaml::Hash(fields))
}

fn name_arg(args: &[Yaml], at: usize) -> Result<&str, Fault> {
    match &args[at] {
        Yaml::String(name) if !name.is_empty() => Ok(name),
        other => Err(Fault::expected(at, "a non-empty class name", other)),
    }
}

fn hash_arg(args: &[Yaml], at: usize) -> Result<&Hash, Fault> {
    match &args[at] {
        Yaml::Hash(h) => Ok(h),
        other => Err(Fault::expected(at, "map", other)),
    }
}

/// The declared fields of the record type at `at`.
fn fields_arg(args: &[Yaml], at: usize) -> Result<&Hash, Fault> {
    match &args[at] {
        Yaml::Hash(map) if map.get(&Yaml::String("type".to_string())) == Some(&Yaml::String("record".to_string())) => match map.get(&Yaml::String("fields".to_string())) {
            Some(Yaml::Hash(fields)) => Ok(fields),
            _ => Err(Fault::at(at, "type", "malformed record type".into())),
        },
        other => Err(Fault::expected(at, "a record type of fields", other)),
    }
}

fn parents_arg(args: &[Yaml], at: usize) -> Result<Vec<Class>, Fault> {
    match &args[at] {
        Yaml::Array(items) => items.iter().map(|item| Class::from_yaml(item, at)).collect(),
        other => Err(Fault::expected(at, "list of classes", other)),
    }
}

impl Class {
    fn from_yaml(class: &Yaml, arg: usize) -> Result<Class, Fault> {
        let bad = || Fault::at(arg, "type", "expected a class".into());
        let Yaml::Hash(map) = class else { return Err(bad()) };
        let get = |k: &str| map.get(&Yaml::String(k.to_string()));
        let (Some(Yaml::String(name)), Some(Yaml::Hash(fields)), Some(Yaml::Hash(values)), Some(Yaml::Hash(prov)), Some(Yaml::Array(parents)), Some(Yaml::Boolean(concrete))) =
            (get("name"), get("fields"), get("values"), get("provenance"), get("parents"), get("concrete"))
        else {
            return Err(bad());
        };
        if get("type") != Some(&Yaml::String("class".to_string())) {
            return Err(bad());
        }
        let source = |field: &str, which: &str| match prov.get(&Yaml::String(field.to_string())) {
            Some(Yaml::Hash(entry)) => match entry.get(&Yaml::String(which.to_string())) {
                Some(Yaml::String(by)) => Ok(by.clone()),
                _ => Err(bad()),
            },
            _ => Err(bad()),
        };
        let constraints = match get("constraints") {
            Some(Yaml::Array(items)) => items.clone(),
            _ => Vec::new(),
        };
        let slots = fields
            .iter()
            .map(|(field, spec)| {
                let Yaml::String(field) = field else { return Err(bad()) };
                let value = values.get(&Yaml::String(field.to_string())).map(|v| Ok((v.clone(), source(field, "value")?))).transpose()?;
                Ok(Slot { name: field.clone(), spec: spec.clone(), declared_by: source(field, "type")?, value })
            })
            .collect::<Result<_, _>>()?;
        let parents = parents.iter().map(|p| if let Yaml::String(p) = p { Ok(p.clone()) } else { Err(bad()) }).collect::<Result<_, _>>()?;
        Ok(Class { name: name.clone(), parents, slots, constraints, concrete: *concrete })
    }

    fn to_yaml(&self) -> Yaml {
        let (mut fields, mut values, mut provenance) = (Hash::new(), Hash::new(), Hash::new());
        for slot in &self.slots {
            fields.insert(Yaml::String(slot.name.to_string()), slot.spec.clone());
            let mut origin = Hash::new();
            origin.insert(Yaml::String("type".to_string()), Yaml::String(slot.declared_by.to_string()));
            if let Some((value, by)) = &slot.value {
                values.insert(Yaml::String(slot.name.to_string()), value.clone());
                origin.insert(Yaml::String("value".to_string()), Yaml::String(by.to_string()));
            }
            provenance.insert(Yaml::String(slot.name.to_string()), Yaml::Hash(origin));
        }
        let mut class = descriptor("class", [("name", Yaml::String(self.name.to_string())), ("parents", Yaml::Array(self.parents.iter().map(|p| Yaml::String(p.to_string())).collect()))]);
        if let Yaml::Hash(map) = &mut class {
            for (k, v) in [("fields", fields), ("values", values)] {
                map.insert(Yaml::String(k.to_string()), Yaml::Hash(v));
            }
            if !self.constraints.is_empty() {
                map.insert(Yaml::String("constraints".to_string()), Yaml::Array(self.constraints.clone()));
            }
            map.insert(Yaml::String("provenance".to_string()), Yaml::Hash(provenance));
            map.insert(Yaml::String("concrete".to_string()), Yaml::Boolean(self.concrete));
        }
        class
    }
}

fn conflict(field: &str, first: &str, second: &str, what: &str, resolver: &str) -> Fault {
    let message = format!("field `{field}` has {what} from both `{first}` and `{second}`; resolve it in `{resolver}`");
    let data = mapping([("field", Yaml::String(field.to_string())), ("sources", Yaml::Array(vec![Yaml::String(first.to_string()), Yaml::String(second.to_string())]))]);
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
    let own_order: Vec<String> = fields.keys().map(|k| if let Yaml::String(k) = k { k.clone() } else { String::new() }).collect();
    let mut slots = Vec::new();
    for field in order.iter().map(|f| f.to_string()).chain(own_order.into_iter().filter(|f| !parents.iter().any(|p| p.slots.iter().any(|s| s.name == *f)))) {
        let inherited: Vec<&Slot> = parents.iter().filter_map(|p| p.slots.iter().find(|s| s.name == field)).collect();
        let own_spec = fields.get(&Yaml::String(field.to_string()));
        let own_value = values.get(&Yaml::String(field.to_string()));
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
            Some(v) => Some((v.clone(), name.to_string())),
            None => {
                let supplied: Vec<&(Yaml, String)> = inherited.iter().filter_map(|s| s.value.as_ref()).collect();
                match supplied.iter().find(|other| !equal(&other.0, &supplied[0].0)) {
                    Some(other) => return Err(conflict(&field, &supplied[0].1, &other.1, "different values", name)),
                    None => supplied.first().map(|v| (*v).clone()),
                }
            }
        };
        slots.push(Slot { name: field, spec, declared_by, value });
    }
    if let Some(stray) = values.keys().find(|k| !slots.iter().any(|slot| Yaml::String(slot.name.to_string()) == **k)) {
        let Yaml::String(stray) = stray else { unreachable!("value names are strings") };
        return Err(Fault::at(at.values, "type", format!("`{name}` sets `{stray}`, which no parent or field declares")));
    }
    let mut constraints: Vec<Yaml> = Vec::new();
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
fn narrow(field: &str, spec: &Yaml, inherited: &[&Slot], name: &str, arg: usize, pred: &mut Pred) -> Result<(), Fault> {
    let own = Field::parse(spec).map_err(|why| Fault::at(arg, "type", why))?;
    for slot in inherited {
        let theirs = Field::parse(&slot.spec).map_err(|why| Fault::at(1, "type", why))?;
        if !subtype(&own.ty, &theirs.ty, pred) || (!own.required && theirs.required) {
            let message = format!("`{name}` redeclares field `{field}` in a way that does not fit the declaration from `{}`", slot.declared_by);
            let data = mapping([("field", Yaml::String(field.to_string())), ("sources", Yaml::Array(vec![Yaml::String(slot.declared_by.to_string()), Yaml::String(name.to_string())]))]);
            return Err(Fault { cause: None, kind: "conflict".into(), message, arg: Some(arg), data: Some(data) });
        }
    }
    Ok(())
}

/// Every set value must fit its field's type; all misfits are reported together.
fn validate(class: &Class, pred: &mut Pred) -> Result<(), Fault> {
    let mut found = Vec::new();
    for slot in &class.slots {
        let Some((value, _)) = &slot.value else { continue };
        let ty = Field::parse(&slot.spec).map_err(|why| Fault::at(2, "type", why))?.ty;
        found.extend(check_slot(&slot.name, value, &ty, pred));
    }
    if found.is_empty() {
        return Ok(());
    }
    let message = format!("{} misfit{} in the values of `{}`: {}", found.len(), if found.len() == 1 { "" } else { "s" }, class.name, found[0].1);
    Err(Fault { cause: None, kind: "type".into(), message, arg: None, data: Some(mapping([("violations", Yaml::Array(found.into_iter().map(|f| f.0).collect()))])) })
}

fn check_slot(field: &str, value: &Yaml, ty: &Ty, pred: &mut Pred) -> Vec<(Yaml, String)> {
    check_ty(value, ty, pred)
        .into_iter()
        .map(|v| {
            let at = format!("/{field}{}", if v.at() == "/" { String::new() } else { v.at().to_string() });
            (mapping([("at", Yaml::String(at.to_string())), ("message", Yaml::String(v.message().to_string()))]), format!("{at}: {}", v.message()))
        })
        .collect()
}

/// Whole-record constraints run on the realized values; each failure is reported.
fn constrained(class: &Class, pred: &mut Pred) -> Result<(), Fault> {
    let values = Yaml::Hash(class.slots.iter().filter_map(|slot| slot.value.as_ref().map(|(v, _)| (Yaml::String(slot.name.to_string()), v.clone()))).collect());
    let mut found = Vec::new();
    for (i, check) in class.constraints.iter().enumerate() {
        let why = match pred(check, &values) {
            Ok(true) => continue,
            Ok(false) => format!("constraint {i} of `{}` does not hold", class.name),
            Err(why) => format!("constraint {i} of `{}` failed to run: {why}", class.name),
        };
        found.push(why);
    }
    if found.is_empty() {
        return Ok(());
    }
    let entries = found.iter().map(|why| mapping([("at", Yaml::String("/".to_string())), ("message", Yaml::String(why.to_string()))])).collect();
    Err(Fault { cause: None, kind: "type".into(), message: found[0].clone(), arg: None, data: Some(mapping([("violations", Yaml::Array(entries))])) })
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
            Some(default) => slot.value = Some((default, format!("default of {}", slot.declared_by))),
            None if field.required => missing.push(slot.clone()),
            None => {}
        }
    }
    if !missing.is_empty() {
        let list = missing.iter().map(|m| format!("`{}` (required by {})", m.name, m.declared_by)).collect::<Vec<_>>().join(", ");
        let entries = missing.iter().map(|m| mapping([("field", Yaml::String(m.name.to_string())), ("required_by", Yaml::String(m.declared_by.to_string()))])).collect();
        let message = format!("class `{}` is abstract: missing {list}", class.name);
        return Err(Fault { cause: None, kind: "abstract".into(), message, arg, data: Some(mapping([("missing", Yaml::Array(entries))])) });
    }
    validate(&class, pred)?;
    constrained(&class, pred)?;
    class.concrete = true;
    Ok(class)
}

/// The values of a realized class, for machine-level builtins.
pub(super) fn concrete_values(class: &Yaml, arg: usize) -> Result<Hash, Fault> {
    let parsed = Class::from_yaml(class, arg)?;
    if !parsed.concrete {
        return Err(Fault::at(arg, "abstract", format!("class `{}` is abstract; realize it first", parsed.name)));
    }
    Ok(parsed.slots.into_iter().filter_map(|slot| slot.value.map(|(v, _)| (Yaml::String(slot.name.to_string()), v))).collect())
}
