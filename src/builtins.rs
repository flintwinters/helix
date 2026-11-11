//! Builtin actors as pure functions over evaluated values. The VM owns evaluation order
//! (including the lazy `if`); this module owns arity, typing, and the arithmetic itself.

use yaml_rust2::Yaml;

/// A builtin failure; `arg` is the index of the offending argument, or `None` for the whole expression.
pub struct Fault {
    pub kind: &'static str,
    pub message: String,
    pub arg: Option<usize>,
}

impl Fault {
    fn at(arg: usize, kind: &'static str, message: String) -> Fault {
        Fault { kind, message, arg: Some(arg) }
    }

    fn whole(kind: &'static str, message: String) -> Fault {
        Fault { kind, message, arg: None }
    }
}

/// `(minimum, maximum)` argument counts; `None` for an unbounded maximum. `None` overall means not a builtin.
pub fn arity(actor: &str) -> Option<(usize, Option<usize>)> {
    match actor {
        "add" | "mul" => Some((2, None)),
        "sub" | "div" | "mod" | "eq" | "ne" | "lt" | "le" | "gt" | "ge" => Some((2, Some(2))),
        "not" => Some((1, Some(1))),
        "if" => Some((3, Some(3))),
        _ => None,
    }
}

/// Apply a strict (eagerly evaluated) builtin. `if` is lazy and handled by the VM.
pub fn apply(actor: &str, args: &[Yaml]) -> Result<Yaml, Fault> {
    match actor {
        "add" | "sub" | "mul" | "div" | "mod" => arithmetic(actor, &integers(args)?).map(Yaml::Integer),
        "eq" => Ok(Yaml::Boolean(args[0] == args[1])),
        "ne" => Ok(Yaml::Boolean(args[0] != args[1])),
        "lt" | "le" | "gt" | "ge" => {
            let n = integers(args)?;
            Ok(Yaml::Boolean(match actor {
                "lt" => n[0] < n[1],
                "le" => n[0] <= n[1],
                "gt" => n[0] > n[1],
                _ => n[0] >= n[1],
            }))
        }
        "not" => condition(&args[0]).map(|b| Yaml::Boolean(!b)),
        _ => unreachable!("`{actor}` is not a strict builtin"),
    }
}

/// A boolean operand, as used by `not` and by the `if` condition (argument 0).
pub fn condition(value: &Yaml) -> Result<bool, Fault> {
    match value {
        Yaml::Boolean(b) => Ok(*b),
        other => Err(Fault::at(0, "type", format!("expected boolean, got {}", type_name(other)))),
    }
}

fn integers(args: &[Yaml]) -> Result<Vec<i64>, Fault> {
    args.iter()
        .enumerate()
        .map(|(index, value)| match value {
            Yaml::Integer(i) => Ok(*i),
            other => Err(Fault::at(index, "type", format!("expected integer, got {}", type_name(other)))),
        })
        .collect()
}

/// Left fold over checked i64 operations; `div` and `mod` truncate toward zero like Rust's `/` and `%`.
fn arithmetic(actor: &str, operands: &[i64]) -> Result<i64, Fault> {
    let overflow = || Fault::whole("overflow", format!("`{actor}` overflowed i64"));
    operands[1..].iter().try_fold(operands[0], |acc, &x| match actor {
        "add" => acc.checked_add(x).ok_or_else(overflow),
        "sub" => acc.checked_sub(x).ok_or_else(overflow),
        "mul" => acc.checked_mul(x).ok_or_else(overflow),
        "div" | "mod" if x == 0 => Err(Fault::whole("divide_by_zero", format!("`{actor}` by zero"))),
        "div" => acc.checked_div(x).ok_or_else(overflow),
        _ => acc.checked_rem(x).ok_or_else(overflow),
    })
}

fn type_name(v: &Yaml) -> &'static str {
    match v {
        Yaml::Integer(_) => "integer",
        Yaml::Real(_) => "float",
        Yaml::String(_) => "string",
        Yaml::Boolean(_) => "boolean",
        Yaml::Array(_) => "sequence",
        Yaml::Hash(_) => "mapping",
        Yaml::Null => "null",
        _ => "invalid value",
    }
}
