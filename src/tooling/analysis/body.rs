//! Static checking of function bodies and call sites without running anything. It covers a
//! deliberately small fragment: literals, annotated parameters, arithmetic and comparison,
//! conditions, sequences, declared map/filter callbacks, structural records and constant-key
//! access/update, calls of named functions with annotated parameters, and annotation expressions
//! made of literal type constructors.
//! Unknown record fields retain `any`; other unsupported expressions have no static type.
//!
//! A finding is an `error` only when the program is certain to fail: the inferred type and the
//! expected type have no value in common. When they might agree at run time, or an expression
//! has no static type, the obligation is reported `unchecked` instead of passing silently.
use crate::builtins::{self, describe, function_type, Field, Ty};
use crate::graph::{Effects, Graph, NodeId, Site, Syntax, ROOT};
use crate::model::{string as s, HelixError};
use crate::value::{Hash, Value};
use std::collections::{HashMap, HashSet};
use yaml_rust2::Yaml;

const MAX_TYPE_CONSTRUCTOR_DEPTH: usize = 8;
const MAX_TYPE_SHAPE_NODES: usize = 4096;
// Local precision limits complement the expanded inferred-type shape budget.
const MAX_INFERRED_STRING_BYTES: usize = 256;
const MAX_SEQUENCE_ALTERNATIVES: usize = 32;

/// Count expanded type structure, stopping before a shared source DAG can grow
/// into an unbounded inferred tree. Count retained value slots, but do not walk
/// their shared contents or function environments; this is not a total-memory cap.
fn type_cost(ty: &Ty, limit: usize, byte_limit: usize) -> Option<(usize, usize)> {
    let mut pending = vec![ty];
    let mut size = 0usize;
    let mut bytes = 0usize;
    while let Some(ty) = pending.pop() {
        size = size.checked_add(1)?;
        if size > limit { return None; }
        match ty {
            Ty::List(item) | Ty::Map(item) | Ty::Refine(item, _) => pending.push(item),
            Ty::Fn(params, result, _) => {
                if params.len() + 1 > limit - size { return None; }
                pending.extend(params);
                pending.push(result);
            }
            Ty::Union(members) => {
                if members.len() > limit - size { return None; }
                pending.extend(members);
            }
            Ty::Record(fields) | Ty::Class(fields, ..) => {
                if fields.len() > limit - size { return None; }
                for (name, field) in fields {
                    bytes = bytes.checked_add(name.len())?;
                    size = size.checked_add(usize::from(field.default.is_some()))?;
                }
                if let Ty::Class(_, fixed, constraints) = ty {
                    size = size.checked_add(fixed.len())?.checked_add(constraints.len())?;
                    for (name, _) in fixed { bytes = bytes.checked_add(name.len())?; }
                }
                pending.extend(fields.iter().map(|(_, field)| &field.ty));
            }
            Ty::Enum(values) => {
                size = size.checked_add(values.len())?;
                for value in values {
                    if let Value::String(text) = value { bytes = bytes.checked_add(text.len())?; }
                }
            }
            Ty::Any | Ty::Never | Ty::Int | Ty::Float | Ty::Str | Ty::Bool
                | Ty::Null | Ty::Secret | Ty::Function | Ty::Vm => {}
        }
        if size > limit || bytes > byte_limit { return None; }
        if pending.len() > limit - size { return None; }
    }
    Some((size, bytes))
}

/// What the checks found: definite failures, and obligations they could not settle.
#[derive(Default)]
pub(super) struct Findings {
    pub errors: Vec<HelixError>,
    pub unchecked: Vec<HelixError>,
}

pub(super) fn check(graph: &Graph) -> Findings {
    let mut checker = Checker { graph, memo: HashMap::new(), active: HashSet::new(), findings: Findings::default(), type_memo: HashMap::new() };
    let mut pending = vec![ROOT];
    while let Some(id) = pending.pop() {
        match graph.syntax(id) {
            Syntax::Call(items) => {
                checker.infer(id);
                match graph.form(id) {
                    Some("fn") => checker.function(id),
                    Some("type_time") => checker.region(id),
                    _ => {}
                }
                // A computed head contains executable source just like an argument.
                pending.extend(items.iter().rev());
            }
            Syntax::Mapping(entries) => pending.extend(entries.iter().rev().map(|(_, id)| *id)),
            Syntax::Sequence(items) => pending.extend(items.iter().rev()),
            _ => {}
        }
    }
    checker.findings
}

struct Checker<'g> {
    graph: &'g Graph,
    memo: HashMap<NodeId, Option<Ty>>,
    active: HashSet<NodeId>,
    findings: Findings,
    type_memo: HashMap<(NodeId, usize), Option<Value>>,
}

impl Checker<'_> {
    fn error(&mut self, at: NodeId, message: String) {
        self.findings.errors.push(self.graph.error(at, "type", message));
    }
    fn unchecked(&mut self, at: NodeId, message: String) {
        self.findings.unchecked.push(self.graph.error(at, "unchecked", message));
    }

    /// The static type of an expression, checking its operands on the way. Each node is checked once.
    fn infer(&mut self, id: NodeId) -> Option<Ty> {
        if let Some(known) = self.memo.get(&id) {
            return known.clone();
        }
        if !self.active.insert(id) {
            return None;
        }
        let found = self.infer_node(id).filter(|ty|
            type_cost(ty, MAX_TYPE_SHAPE_NODES, crate::model::MAX_VALUE_SIZE).is_some());
        self.active.remove(&id);
        self.memo.insert(id, found.clone());
        found
    }

    fn infer_node(&mut self, id: NodeId) -> Option<Ty> {
        match self.graph.syntax(id) {
            Syntax::Scalar(Yaml::Integer(_)) => Some(Ty::Int),
            Syntax::Scalar(Yaml::Real(_)) => Some(Ty::Float),
            Syntax::Scalar(Yaml::String(text)) => Some(if text.len() <= MAX_INFERRED_STRING_BYTES {
                Ty::Enum(vec![s(text)])
            } else {
                Ty::Str
            }),
            Syntax::Scalar(Yaml::Boolean(_)) => Some(Ty::Bool),
            Syntax::Scalar(Yaml::Null) => Some(Ty::Null),
            Syntax::Reference(name) if !name.contains('.') => match self.graph.lexical_site(id, name)? {
                Site::Parameter(function, index) => self.parameter_type(function, index),
                Site::Entry(target) => self.infer(target),
            },
            Syntax::Call(items) => self.infer_call(id, items),
            Syntax::Sequence(items) => {
                let mut members = Vec::new();
                let mut widened = false;
                let mut remaining = MAX_TYPE_SHAPE_NODES - 2; // list and possible union
                let mut remaining_bytes = crate::model::MAX_VALUE_SIZE;
                for &item in items {
                    let ty = self.infer(item).unwrap_or(Ty::Any);
                    // Continue checking every item after widening, retaining
                    // diagnostics without collecting further alternatives.
                    if widened { continue; }
                    // Compact homogeneous runs without quadratic shape comparisons.
                    if members.last() != Some(&ty) {
                        let cost = type_cost(&ty, remaining, remaining_bytes);
                        if members.len() == MAX_SEQUENCE_ALTERNATIVES || cost.is_none() {
                            members.clear();
                            members.push(Ty::Any);
                            widened = true;
                        } else {
                            let (nodes, bytes) = cost.unwrap();
                            remaining -= nodes;
                            remaining_bytes -= bytes;
                            members.push(ty);
                        }
                    }
                }
                Some(Ty::List(Box::new(match members.len() {
                    0 => Ty::Never,
                    1 => members.pop().unwrap(),
                    _ => Ty::Union(members),
                })))
            }
            Syntax::Mapping(entries) => {
                let mut fields = Vec::new();
                let mut remaining = Some(MAX_TYPE_SHAPE_NODES - 1);
                let mut remaining_bytes = crate::model::MAX_VALUE_SIZE;
                for (name, node) in entries {
                    let ty = self.infer(*node).unwrap_or(Ty::Any);
                    if let Some(left) = remaining {
                        let cost = remaining_bytes.checked_sub(name.len())
                            .and_then(|bytes| type_cost(&ty, left, bytes));
                        if let Some((nodes, bytes)) = cost {
                            remaining = Some(left - nodes);
                            remaining_bytes -= name.len() + bytes;
                            fields.push((name.clone(), Field { ty, required: true, default: None }));
                        } else {
                            remaining = None;
                            fields.clear();
                        }
                    }
                }
                Some(if remaining.is_some() { Ty::Record(fields) } else { Ty::Map(Box::new(Ty::Any)) })
            }
            _ => None,
        }
    }

    fn parameter_type(&mut self, function: NodeId, index: usize) -> Option<Ty> {
        let (_, annotation) = self.graph.parameters(function).ok()?.into_iter().nth(index)?;
        self.annotation(annotation?)
    }

    fn infer_call(&mut self, id: NodeId, items: &[NodeId]) -> Option<Ty> {
        let operands = &items[1..];
        let head = self.graph.identifier(items[0]);
        let Some(actor) = head.and_then(builtins::descriptor) else {
            return self.infer_user_call(id, items);
        };
        // The source pass owns arity diagnostics; it still invokes this pass
        // after finding them, so malformed calls must not index missing operands.
        let (min, max) = actor.arity;
        if operands.len() < min || max.is_some_and(|max| operands.len() > max) {
            return None;
        }
        let number = Ty::Union(vec![Ty::Int, Ty::Float]);
        match actor.name {
            "add" | "sub" | "mul" | "div" | "mod" | "neg" => {
                let types = operands.iter().map(|&operand| self.expect(operand, actor.name, &number)).collect::<Vec<_>>();
                Some(match types.into_iter().collect::<Option<Vec<_>>>() {
                    Some(all) if all.iter().all(|t| *t == Ty::Int) => Ty::Int,
                    Some(all) if all.iter().any(|t| *t == Ty::Float) && all.iter().all(|t| t.below(&number)) => Ty::Float,
                    _ => number,
                })
            }
            "lt" | "le" | "gt" | "ge" => {
                let ordered = Ty::Union(vec![Ty::Int, Ty::Float, Ty::Str]);
                operands.iter().for_each(|&operand| drop(self.expect(operand, actor.name, &ordered)));
                Some(Ty::Bool)
            }
            "not" | "and" | "or" => {
                operands.iter().for_each(|&operand| drop(self.expect(operand, actor.name, &Ty::Bool)));
                Some(Ty::Bool)
            }
            "if" => {
                self.expect(operands[0], "if", &Ty::Bool);
                let (then, otherwise) = (self.infer(operands[1]), self.infer(operands[2]));
                Some(Ty::Union(vec![then?, otherwise?]))
            }
            "let" => self.infer(operands[1]),
            "type_time" => self.infer(operands[0]),
            "map" | "filter" => self.collection(actor.name, operands[0], operands[1]),
            "get" if operands.len() == 2 => {
                let Value::String(key) = self.scalar(operands[1])? else { return None };
                let Ty::Record(fields) = self.infer(operands[0])? else { return None };
                fields.into_iter().find(|(name, field)| name == &key && field.required).map(|(_, field)| field.ty)
            }
            "set" if operands.len() == 3 => {
                let Value::String(key) = self.scalar(operands[1])? else { return None };
                let Ty::Record(mut fields) = self.infer(operands[0])? else { return None };
                let field = Field { ty: self.infer(operands[2]).unwrap_or(Ty::Any), required: true, default: None };
                if let Some((_, previous)) = fields.iter_mut().find(|(name, _)| name == &key) {
                    *previous = field;
                } else {
                    fields.push((key, field));
                }
                Some(Ty::Record(fields))
            }
            "eq" | "ne" | "is" | "has" | "subtype" => Some(Ty::Bool),
            "str" | "type" => Some(Ty::Str),
            "int" | "len" => Some(Ty::Int),
            "float" => Some(Ty::Float),
            "fn" => self.function_type(id),
            _ => None,
        }
    }

    /// Iteration calls its callback only when an element exists. Collection
    /// annotations permit empty lists, so only literal nonempty inputs justify
    /// definite invocation errors; no contextual callback-body inference is used.
    fn collection(&mut self, actor: &str, callback: NodeId, input: NodeId) -> Option<Ty> {
        let list = Ty::List(Box::new(Ty::Any));
        let actual = self.expect(input, actor, &list);
        let Some(Ty::List(element)) = actual else {
            if actual.as_ref().is_none_or(|ty| ty.overlaps(&list)) {
                self.unchecked(input, format!("`{actor}` input has no known list element type"));
            }
            return None;
        };
        let nonempty = match self.graph.syntax(input) {
            Syntax::Sequence(items) if items.is_empty() => return Some(Ty::List(Box::new(Ty::Never))),
            Syntax::Sequence(_) => true,
            _ => false,
        };
        let signature = self.infer(callback);
        let Some(Ty::Fn(params, result, _)) = signature else {
            match signature {
                Some(ty) if !ty.overlaps(&Ty::Function) && nonempty =>
                    self.error(callback, format!("`{actor}` callback is {}, expected function", ty.name())),
                _ => self.unchecked(callback, format!("`{actor}` callback has no known callable signature")),
            }
            return if actor == "filter" { Some(Ty::List(element)) } else { None };
        };
        if params.len() != 1 {
            let message = format!("`{actor}` callback takes {} arguments, expected 1", params.len());
            if nonempty { self.error(callback, message); } else { self.unchecked(callback, message); }
        } else {
            self.callback_type(callback, actor, "argument", &element, &params[0], nonempty);
        }
        if actor == "filter" {
            self.callback_type(callback, actor, "result", &result, &Ty::Bool, nonempty);
            Some(Ty::List(element))
        } else {
            if *result == Ty::Any {
                self.unchecked(callback, "`map` callback result has no more specific static type than any".into());
            }
            Some(Ty::List(result))
        }
    }

    fn callback_type(&mut self, at: NodeId, actor: &str, part: &str, actual: &Ty, expected: &Ty, nonempty: bool) {
        if actual.below(expected) { return; }
        let message = format!("`{actor}` callback {part} is {}, expected {}", actual.name(), expected.name());
        if nonempty && !actual.overlaps(expected) { self.error(at, message); }
        else { self.unchecked(at, message); }
    }

    /// An operand's type, reporting a definite mismatch with `expected` and noting a possible one.
    fn expect(&mut self, operand: NodeId, what: &str, expected: &Ty) -> Option<Ty> {
        let actual = self.infer(operand);
        match &actual {
            Some(actual) if !actual.overlaps(expected) => {
                self.error(operand, format!("`{what}` expects {}, got {}", expected.name(), actual.name()));
            }
            Some(actual) if !actual.below(expected) => {
                self.unchecked(operand, format!("`{what}` expects {}; this is {}", expected.name(), actual.name()));
            }
            _ => {}
        }
        actual
    }

    /// A call of a function the source declares, or of a parameter typed as a function.
    fn infer_user_call(&mut self, id: NodeId, items: &[NodeId]) -> Option<Ty> {
        let name = self.graph.identifier(items[0])?;
        let callee = match self.graph.lexical_base(items[0], name)? {
            (Site::Parameter(function, index), None) => self.parameter_type(function, index)?,
            (Site::Parameter(..), Some(_)) => return None,
            (Site::Entry(_), _) => {
                let target = self.graph.named_node(items[0], name, &|node| node)?;
                self.function_type_of(target)?
            }
        };
        let Ty::Fn(params, result, _) = callee else {
            return None;
        };
        if params.len() != items.len() - 1 {
            self.error(id, format!("`{name}` takes {} argument{}, got {}", params.len(), if params.len() == 1 { "" } else { "s" }, items.len() - 1));
            return Some(*result);
        }
        for (position, (&arg, expected)) in items[1..].iter().zip(&params).enumerate() {
            self.argument(arg, &format!("argument {} of `{name}`", position + 1), expected);
        }
        Some(*result)
    }

    fn argument(&mut self, arg: NodeId, what: &str, expected: &Ty) {
        if *expected == Ty::Any {
            return;
        }
        match self.infer(arg) {
            Some(actual) if !actual.overlaps(expected) => self.error(arg, format!("{what} is {}, expected {}", actual.name(), expected.name())),
            Some(actual) if !actual.below(expected) => self.unchecked(arg, format!("{what} may not be {}; it is {}", expected.name(), actual.name())),
            Some(_) => {}
            None => self.unchecked(arg, format!("{what} has no static type; expected {}", expected.name())),
        }
    }

    /// The signature a `fn` declares; unannotated parameters and results are `any`. Pure when the
    /// source proves no effect.
    fn function_type(&mut self, function: NodeId) -> Option<Ty> {
        let params = self.graph.parameters(function).ok()?;
        let mut remaining = MAX_TYPE_SHAPE_NODES - 1;
        let mut remaining_bytes = crate::model::MAX_VALUE_SIZE;
        let mut declared = Vec::new();
        for (_, annotation) in params {
            let ty = annotation.map_or(Some(Ty::Any), |a| self.annotation(a))?;
            let (nodes, bytes) = type_cost(&ty, remaining, remaining_bytes)?;
            remaining -= nodes;
            remaining_bytes -= bytes;
            declared.push(ty);
        }
        let result = match self.graph.call(function)?.get(3) {
            Some(&annotation) => self.annotation(annotation)?,
            None => Ty::Any,
        };
        type_cost(&result, remaining, remaining_bytes)?;
        let pure = self.graph.effects(function, &|_| None) == Effects::None;
        Some(Ty::Fn(declared, Box::new(result), pure))
    }
    fn function_type_of(&mut self, node: NodeId) -> Option<Ty> {
        if self.graph.form(node) == Some("fn") { self.function_type(node) } else { None }
    }

    /// Locate a returned record member through wrappers whose result is exactly
    /// their body. Do not guess origins through references or conditional paths.
    fn result_field(&self, mut node: NodeId, field: &str) -> Option<NodeId> {
        loop {
            match self.graph.syntax(node) {
                Syntax::Mapping(entries) => return entries.iter()
                    .find(|(name, _)| name == field).map(|(_, value)| *value),
                Syntax::Call(items) => node = match (self.graph.form(node), items.len()) {
                    (Some("let"), 3) => items[2],
                    (Some("type_time"), 2) => items[1],
                    _ => return None,
                },
                _ => return None,
            }
        }
    }

    /// An annotated function's body must be able to produce its declared result.
    fn function(&mut self, function: NodeId) {
        let Some(items) = self.graph.call(function) else { return };
        let (Some(&body), Some(&annotation)) = (items.get(2), items.get(3)) else { return };
        let Some(declared) = self.annotation(annotation) else {
            return self.unchecked(annotation, "the declared result annotation is outside the supported static type fragment, so the body is not checked against it".into());
        };
        let path = self.graph.path(function);
        match self.infer(body) {
            Some(actual) if !actual.overlaps(&declared) => {
                let field = actual.disjoint_field(&declared);
                let at = field.as_ref().and_then(|field| self.result_field(body, field)).unwrap_or(body);
                let field = field.map_or(String::new(), |name| format!(" at field `{name}`"));
                self.error(at, format!("the body of `{path}` is {}, declared {}{field}", actual.name(), declared.name()));
            }
            Some(actual) if !actual.below(&declared) => self.unchecked(body, format!("the body of `{path}` may not be {}; it is {}", declared.name(), actual.name())),
            Some(_) => {}
            None => self.unchecked(body, format!("the body of `{path}` has no static type; declared {}", declared.name())),
        }
    }

    /// Code a `type_time` region can reach must be free of effects, or it fails when run.
    fn region(&mut self, region: NodeId) {
        match self.graph.region_effects(region) {
            Effects::None => {}
            Effects::Requests(at) => self.error(at, "`effect` is reachable from a `type_time` region, which rejects effects".into()),
            Effects::Unproven(at) => self.unchecked(at, "a `type_time` region reaches a call the analysis cannot see through, so its purity is not proved".into()),
        }
    }

    /// The type a literal annotation expression denotes.
    fn annotation(&mut self, id: NodeId) -> Option<Ty> {
        let value = self.type_value(id, 0)?;
        // Descriptor DAGs share values, but Ty::parse expands their structure.
        // Bound that expanded work before asking the authoritative parser.
        let mut pending = vec![&value];
        let mut remaining = MAX_TYPE_SHAPE_NODES;
        let mut remaining_bytes = crate::model::MAX_VALUE_SIZE;
        while let Some(value) = pending.pop() {
            remaining = remaining.checked_sub(1)?;
            match value {
                Value::Array(items) => {
                    if items.len() > remaining.saturating_sub(pending.len()) { return None; }
                    pending.extend(items.iter());
                }
                Value::Hash(entries) => {
                    if entries.len().checked_mul(2)? > remaining.saturating_sub(pending.len()) { return None; }
                    pending.extend(entries.iter().flat_map(|(key, value)| [key, value]));
                }
                Value::String(text) => remaining_bytes = remaining_bytes.checked_sub(text.len())?,
                _ => {}
            }
        }
        Ty::parse(&value).ok()
    }

    fn type_value(&mut self, id: NodeId, depth: usize) -> Option<Value> {
        if depth > MAX_TYPE_CONSTRUCTOR_DEPTH {
            return None;
        }
        if let Some(value) = self.type_memo.get(&(id, depth)) {
            return value.clone();
        }
        let mut target = id;
        let mut aliases = HashSet::new();
        let value = loop {
            if !aliases.insert(target) {
                break None;
            }
            let name = match self.graph.syntax(target) {
                Syntax::Reference(name) => name,
                // Ordinary strings are type values, never implicit lexical references.
                Syntax::Scalar(Yaml::String(name)) => break Some(s(name)),
                _ => break self.type_constructor(target, depth),
            };
            match self.graph.lexical_site(target, name) {
                None if builtins::is_primitive(name) => break Some(s(name)),
                Some(Site::Entry(next)) => {
                    if let Some(value) = self.type_memo.get(&(next, depth)) {
                        break value.clone();
                    }
                    target = next;
                }
                _ => break None,
            }
        };
        for alias in aliases {
            self.type_memo.insert((alias, depth), value.clone());
        }
        value
    }

    fn type_constructor(&mut self, id: NodeId, depth: usize) -> Option<Value> {
        match self.graph.syntax(id) {
            Syntax::Call(items) => {
                let args = &items[1..];
                match (self.graph.identifier(items[0])?, args.len()) {
                    ("record", count) if count % 2 == 0 => {
                        let mut fields = Hash::new();
                        for pair in args.chunks_exact(2) {
                            let name @ Value::String(_) = self.scalar(pair[0])? else { return None };
                            if fields.insert(name, self.type_value(pair[1], depth + 1)?).is_some() {
                                return None;
                            }
                        }
                        Some(describe("record", [("fields", Value::Hash(fields))]))
                    }
                    ("with_field", 3) => {
                        let Value::Hash(record) = self.type_value(args[0], depth + 1)? else { return None };
                        if record.get(&s("type")) != Some(&s("record")) {
                            return None;
                        }
                        let Value::Hash(mut fields) = record.get(&s("fields"))?.clone() else { return None };
                        let name @ Value::String(_) = self.scalar(args[1])? else { return None };
                        fields.insert(name, self.type_value(args[2], depth + 1)?);
                        Some(describe("record", [("fields", Value::Hash(fields))]))
                    }
                    ("list_of", 1) => Some(describe("list", [("of", self.type_value(args[0], depth + 1)?)])),
                    ("map_of", 1) => Some(describe("map", [("of", self.type_value(args[0], depth + 1)?)])),
                    ("optional", 1) => Some(describe("optional", [("of", self.type_value(args[0], depth + 1)?)])),
                    ("union", 1..) => Some(describe("union", [("of", Value::Array(args.iter().map(|&arg| self.type_value(arg, depth + 1)).collect::<Option<Vec<_>>>()?.into()))])),
                    ("enum", 1..) => Some(describe("enum", [("values", Value::Array(args.iter().map(|&arg| self.scalar(arg)).collect::<Option<Vec<_>>>()?.into()))])),
                    ("literal", 1) => Some(describe("literal", [("value", self.scalar(args[0])?)])),
                    (kind @ ("function_of" | "pure_function_of"), 2) => {
                        let Syntax::Sequence(params) = self.graph.syntax(args[0]) else { return None };
                        let params = params.iter().map(|&param| self.type_value(param, depth + 1)).collect::<Option<Vec<_>>>()?;
                        Some(function_type(params, self.type_value(args[1], depth + 1)?, kind == "pure_function_of"))
                    }
                    _ => None,
                }
            }
            _ => None,
        }
    }

    fn scalar(&self, id: NodeId) -> Option<Value> {
        match self.graph.syntax(id) {
            Syntax::Scalar(Yaml::Integer(i)) => Some(Value::Integer(*i)),
            Syntax::Scalar(Yaml::Real(text)) => text.parse().ok().map(Value::Float),
            Syntax::Scalar(Yaml::String(text)) => Some(s(text)),
            Syntax::Scalar(Yaml::Boolean(b)) => Some(Value::Boolean(*b)),
            Syntax::Scalar(Yaml::Null) => Some(Value::Null),
            _ => None,
        }
    }
}
