//! SPIKE: a lowered, slot-resolved evaluator for tiny pure integer programs.
//!
//! `let` binding only `fn`s over plain parameters, whose bodies use integer/boolean literals,
//! parameter reads, `add sub mul lt le gt ge eq ne`, `if`, and calls to those functions. It is
//! derived from the YAML graph and never stored. Anything else, or any error, limit or doubt
//! returns `None` so the stepwise engine runs the program from scratch and owns all semantics.
use crate::graph::{Graph, Kind, Syntax};
use crate::model::{NodeId, Value};
use yaml_rust2::Yaml;

#[derive(Clone, Copy)]
enum Op { Add, Sub, Mul, Lt, Le, Gt, Ge, Eq, Ne }
#[derive(Clone, Copy, PartialEq)]
enum V { I(i64), B(bool) }
enum E { Const(V), Local(usize), Bin(Op, Box<(E, E)>), If(Box<(E, E, E)>), Call(usize, Vec<E>) }
struct F { body: E }

const MAX_ARITY: usize = 4;
const MAX_STEPS: u64 = 1_000_000;
const MAX_DEPTH: usize = 100;

struct Lower<'g> { graph: &'g Graph, functions: Vec<String>, arities: Vec<usize> }

impl Lower<'_> {
    fn expr(&self, id: NodeId, params: &[&str]) -> Option<E> {
        match self.graph.syntax(id) {
            Syntax::Scalar(Yaml::Integer(i)) => Some(E::Const(V::I(*i))),
            Syntax::Scalar(Yaml::Boolean(b)) => Some(E::Const(V::B(*b))),
            Syntax::Reference(name) => Some(E::Local(params.iter().rposition(|p| *p == name)?)),
            Syntax::Call(items) => {
                let name = self.graph.identifier(items[0])?;
                if let Some(index) = params.iter().position(|p| *p == name) { let _ = index; return None; }
                let args = &items[1..];
                if let Some(f) = self.functions.iter().position(|f| f == name) {
                    if args.len() != self.arities[f] { return None; }
                    return Some(E::Call(f, args.iter().map(|a| self.expr(*a, params)).collect::<Option<_>>()?));
                }
                if name == "if" && args.len() == 3 {
                    return Some(E::If(Box::new((self.expr(args[0], params)?, self.expr(args[1], params)?, self.expr(args[2], params)?))));
                }
                let op = match name {
                    "add" => Op::Add, "sub" => Op::Sub, "mul" => Op::Mul, "lt" => Op::Lt, "le" => Op::Le,
                    "gt" => Op::Gt, "ge" => Op::Ge, "eq" => Op::Eq, "ne" => Op::Ne, _ => return None,
                };
                if args.len() != 2 { return None; }
                Some(E::Bin(op, Box::new((self.expr(args[0], params)?, self.expr(args[1], params)?))))
            }
            _ => None,
        }
    }
}

struct Run<'f> { functions: &'f [F], steps: u64 }

impl Run<'_> {
    fn eval(&mut self, e: &E, frame: &[V; MAX_ARITY], depth: usize) -> Option<V> {
        self.steps += 1;
        if self.steps > MAX_STEPS || depth > MAX_DEPTH { return None; }
        match e {
            E::Const(v) => Some(*v),
            E::Local(i) => Some(frame[*i]),
            E::Bin(op, operands) => {
                let (V::I(a), V::I(b)) = (self.eval(&operands.0, frame, depth)?, self.eval(&operands.1, frame, depth)?) else {
                    return None;
                };
                Some(match op {
                    Op::Add => V::I(a.checked_add(b)?), Op::Sub => V::I(a.checked_sub(b)?), Op::Mul => V::I(a.checked_mul(b)?),
                    Op::Lt => V::B(a < b), Op::Le => V::B(a <= b), Op::Gt => V::B(a > b), Op::Ge => V::B(a >= b),
                    Op::Eq => V::B(a == b), Op::Ne => V::B(a != b),
                })
            }
            E::If(parts) => match self.eval(&parts.0, frame, depth)? {
                V::B(true) => self.eval(&parts.1, frame, depth),
                V::B(false) => self.eval(&parts.2, frame, depth),
                V::I(_) => None,
            },
            E::Call(f, args) => {
                let mut next = [V::I(0); MAX_ARITY];
                for (slot, a) in next.iter_mut().zip(args) { *slot = self.eval(a, frame, depth)?; }
                let functions = self.functions;
                self.eval(&functions[*f].body, &next, depth + 1)
            }
        }
    }
}

/// The program's value when it lies wholly inside the lowered subset and completes within bounds.
pub(crate) fn run(graph: &Graph, root: NodeId) -> Option<Value> {
    let Syntax::Call(items) = graph.syntax(root) else { return None };
    if graph.identifier(items[0])? != "let" || items.len() != 3 { return None; }
    let Kind::Map(bindings) = graph.kind(items[1]) else { return None };
    let mut arities = Vec::new();
    for (_, node) in bindings {
        let Syntax::Call(f) = graph.syntax(*node) else { return None };
        let Kind::Seq(params) = graph.kind(*f.get(1)?) else { return None };
        arities.push(params.len());
    }
    let mut lower = Lower { graph, functions: bindings.iter().map(|(n, _)| n.clone()).collect(), arities };
    let mut functions = Vec::new();
    let names = lower.functions.clone();
    if names.iter().any(|n| crate::builtins::descriptor(n).is_some() || n == "if") { return None; }
    for (_, node) in bindings {
        let Syntax::Call(f) = graph.syntax(*node) else { return None };
        if graph.identifier(f[0])? != "fn" || f.len() != 3 { return None; }
        let Kind::Seq(params) = graph.kind(f[1]) else { return None };
        let params: Vec<&str> = params.iter().map(|p| graph.identifier(*p)).collect::<Option<_>>()?;
        if params.len() > MAX_ARITY { return None; }
        functions.push(F { body: lower.expr(f[2], &params)? });
    }
    let body = lower.expr(items[2], &[])?;
    // Arity must match at every call site; a mismatch is an error the stepwise engine reports.
    let mut run = Run { functions: &functions, steps: 0 };
    match run.eval(&body, &[V::I(0); MAX_ARITY], 0)? {
        V::I(i) => Some(Value::Integer(i)),
        V::B(b) => Some(Value::Boolean(b)),
    }
}
