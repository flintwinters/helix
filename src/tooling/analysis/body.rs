//! Static checking of function bodies and call sites without running anything. It covers a
//! deliberately small fragment: literals, annotated parameters, arithmetic and comparison,
//! conditions, calls of named functions with annotated parameters, and annotation expressions made
//! of literal type constructors. Everything else has no static type.
//!
//! A finding is an `error` only when the program is certain to fail: the inferred type and the
//! expected type have no value in common. When they might agree at run time, or an expression
//! has no static type, the obligation is reported `unchecked` instead of passing silently.
use crate::builtins::{self, describe, function_type, Ty};
use crate::graph::{Effects, Graph, NodeId, Site, Syntax, ROOT};
use crate::model::{string as s, HelixError};
use crate::value::Value;
use std::collections::{HashMap, HashSet};
use yaml_rust2::Yaml;

/// What the checks found: definite failures, and obligations they could not settle.
#[derive(Default)]
pub(super) struct Findings {
    pub errors: Vec<HelixError>,
    pub unchecked: Vec<HelixError>,
}

pub(super) fn check(graph: &Graph) -> Findings {
    let mut checker = Checker { graph, memo: HashMap::new(), active: HashSet::new(), findings: Findings::default() };
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
                pending.extend(items.iter().skip(1).rev());
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
        let found = self.infer_node(id);
        self.active.remove(&id);
        self.memo.insert(id, found.clone());
        found
    }

    fn infer_node(&mut self, id: NodeId) -> Option<Ty> {
        match self.graph.syntax(id) {
            Syntax::Scalar(Yaml::Integer(_)) => Some(Ty::Int),
            Syntax::Scalar(Yaml::Real(_)) => Some(Ty::Float),
            Syntax::Scalar(Yaml::String(_)) => Some(Ty::Str),
            Syntax::Scalar(Yaml::Boolean(_)) => Some(Ty::Bool),
            Syntax::Scalar(Yaml::Null) => Some(Ty::Null),
            Syntax::Reference(name) if !name.contains('.') => match self.graph.lexical_site(id, name)? {
                Site::Parameter(function, index) => self.parameter_type(function, index),
                Site::Entry(target) => self.infer(target),
            },
            Syntax::Call(items) => self.infer_call(id, items),
            _ => None,
        }
    }

    fn parameter_type(&self, function: NodeId, index: usize) -> Option<Ty> {
        let (_, annotation) = self.graph.parameters(function).ok()?.into_iter().nth(index)?;
        self.annotation(annotation?)
    }

    fn infer_call(&mut self, id: NodeId, items: &[NodeId]) -> Option<Ty> {
        let operands = &items[1..];
        let head = self.graph.identifier(items[0]);
        let Some(actor) = head.and_then(builtins::descriptor) else {
            return self.infer_user_call(id, items);
        };
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
            "eq" | "ne" | "is" | "has" | "subtype" => Some(Ty::Bool),
            "str" | "type" => Some(Ty::Str),
            "int" | "len" => Some(Ty::Int),
            "float" => Some(Ty::Float),
            "fn" => self.function_type(id),
            _ => None,
        }
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
        let callee = match self.graph.lexical_site(items[0], name.split('.').next()?)? {
            Site::Parameter(function, index) => self.parameter_type(function, index)?,
            Site::Entry(_) => {
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
        let params = params.into_iter().map(|(_, annotation)| annotation.map_or(Some(Ty::Any), |a| self.annotation(a))).collect::<Option<Vec<_>>>()?;
        let result = match self.graph.call(function)?.get(3) {
            Some(&annotation) => self.annotation(annotation)?,
            None => Ty::Any,
        };
        let pure = self.graph.effects(function, &|_| None) == Effects::None;
        Some(Ty::Fn(params, Box::new(result), pure))
    }
    fn function_type_of(&mut self, node: NodeId) -> Option<Ty> {
        if self.graph.form(node) == Some("fn") { self.function_type(node) } else { None }
    }

    /// An annotated function's body must be able to produce its declared result.
    fn function(&mut self, function: NodeId) {
        let Some(items) = self.graph.call(function) else { return };
        let (Some(&body), Some(&annotation)) = (items.get(2), items.get(3)) else { return };
        let Some(declared) = self.annotation(annotation) else {
            return self.unchecked(annotation, "the declared result is not a literal type, so the body is not checked against it".into());
        };
        let path = self.graph.path(function);
        match self.infer(body) {
            Some(actual) if !actual.overlaps(&declared) => self.error(body, format!("the body of `{path}` is {}, declared {}", actual.name(), declared.name())),
            Some(actual) if !actual.below(&declared) => self.unchecked(body, format!("the body of `{path}` may not be {}; it is {}", declared.name(), actual.name())),
            Some(_) => {}
            None => self.unchecked(body, format!("the body of `{path}` has no static type; declared {}", declared.name())),
        }
    }

    /// Code a `type_time` region can reach must be free of effects, or it fails when run.
    fn region(&mut self, region: NodeId) {
        match self.graph.effects(region, &|_| None) {
            Effects::None => {}
            Effects::Requests(at) => self.error(at, "`effect` is reachable from a `type_time` region, which rejects effects".into()),
            Effects::Unproven(at) => self.unchecked(at, "a `type_time` region reaches a call the analysis cannot see through, so its purity is not proved".into()),
        }
    }

    /// The type a literal annotation expression denotes.
    fn annotation(&self, id: NodeId) -> Option<Ty> {
        Ty::parse(&self.type_value(id, 0)?).ok()
    }

    fn type_value(&self, id: NodeId, depth: usize) -> Option<Value> {
        if depth > 8 {
            return None;
        }
        let ty = |node| self.type_value(node, depth + 1);
        match self.graph.syntax(id) {
            Syntax::Reference(name) => self.named_type(id, name, depth),
            Syntax::Scalar(Yaml::String(name)) => self.named_type(id, name, depth),
            Syntax::Call(items) => {
                let args = &items[1..];
                let all = || args.iter().map(|&arg| ty(arg)).collect::<Option<Vec<_>>>();
                match (self.graph.identifier(items[0])?, args.len()) {
                    ("list_of", 1) => Some(describe("list", [("of", ty(args[0])?)])),
                    ("map_of", 1) => Some(describe("map", [("of", ty(args[0])?)])),
                    ("optional", 1) => Some(describe("optional", [("of", ty(args[0])?)])),
                    ("union", 1..) => Some(describe("union", [("of", Value::Array(all()?.into()))])),
                    ("enum", 1..) => Some(describe("enum", [("values", Value::Array(args.iter().map(|&arg| self.scalar(arg)).collect::<Option<Vec<_>>>()?.into()))])),
                    ("literal", 1) => Some(describe("literal", [("value", self.scalar(args[0])?)])),
                    (kind @ ("function_of" | "pure_function_of"), 2) => {
                        let Syntax::Sequence(params) = self.graph.syntax(args[0]) else { return None };
                        let params = params.iter().map(|&param| ty(param)).collect::<Option<Vec<_>>>()?;
                        Some(function_type(params, ty(args[1])?, kind == "pure_function_of"))
                    }
                    _ => None,
                }
            }
            _ => None,
        }
    }

    /// A primitive name nothing shadows, or a declaration that is itself a literal type.
    fn named_type(&self, from: NodeId, name: &str, depth: usize) -> Option<Value> {
        match self.graph.lexical_site(from, name) {
            None if builtins::is_primitive(name) => Some(s(name)),
            Some(Site::Entry(target)) => self.type_value(target, depth + 1),
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
