//! Integer and float arithmetic, ordering and numeric conversion.

use std::cmp::Ordering;

use yaml_rust2::Yaml;

use super::fault::Fault;
use super::value::as_f64;

#[derive(Clone, Copy)]
enum Num {
    Int(i64),
    Float(f64),
}

impl Num {
    fn float(self) -> f64 {
        match self {
            Num::Int(i) => i as f64,
            Num::Float(f) => f,
        }
    }
}

pub(super) fn apply(actor: &str, args: &[Yaml]) -> Result<Yaml, Fault> {
    match actor {
        "lt" | "le" | "gt" | "ge" => ordering(actor, args),
        "int" => to_int(&args[0]),
        "float" => to_float(&args[0]),
        _ => arithmetic(actor, &(0..args.len()).map(|i| number(args, i)).collect::<Result<Vec<_>, _>>()?),
    }
}

fn number(args: &[Yaml], index: usize) -> Result<Num, Fault> {
    match &args[index] {
        Yaml::Integer(i) => Ok(Num::Int(*i)),
        Yaml::Real(text) => text.parse().map(Num::Float).map_err(|_| Fault::at(index, "type", format!("malformed float `{text}`"))),
        other => Err(Fault::expected(index, "number", other)),
    }
}

/// Floats render with a decimal point or exponent so they re-read as floats.
fn float_yaml(f: f64) -> Yaml {
    Yaml::Real(format!("{f:?}"))
}

/// Integer-only operands stay integers; any float promotes the whole operation to floats.
fn arithmetic(actor: &str, operands: &[Num]) -> Result<Yaml, Fault> {
    let ints: Option<Vec<i64>> = operands.iter().map(|n| if let Num::Int(i) = n { Some(*i) } else { None }).collect();
    match ints {
        Some(ints) => integer_fold(actor, &ints).map(Yaml::Integer),
        None => float_fold(actor, &operands.iter().map(|n| n.float()).collect::<Vec<_>>()).map(float_yaml),
    }
}

/// Left fold over checked i64 operations; `div` and `mod` truncate toward zero like Rust's `/` and `%`.
fn integer_fold(actor: &str, operands: &[i64]) -> Result<i64, Fault> {
    let overflow = || Fault::whole("overflow", format!("`{actor}` overflowed i64"));
    if actor == "neg" {
        return operands[0].checked_neg().ok_or_else(overflow);
    }
    operands[1..].iter().try_fold(operands[0], |acc, &x| match actor {
        "add" => acc.checked_add(x).ok_or_else(overflow),
        "sub" => acc.checked_sub(x).ok_or_else(overflow),
        "mul" => acc.checked_mul(x).ok_or_else(overflow),
        "div" | "mod" if x == 0 => Err(Fault::whole("divide_by_zero", format!("`{actor}` by zero"))),
        "div" => acc.checked_div(x).ok_or_else(overflow),
        _ => acc.checked_rem(x).ok_or_else(overflow),
    })
}

/// Same shape as `integer_fold`; a non-finite result is an `overflow`, never a stored `inf` or `nan`.
fn float_fold(actor: &str, operands: &[f64]) -> Result<f64, Fault> {
    let finite = |f: f64| f.is_finite().then_some(f).ok_or_else(|| Fault::whole("overflow", format!("`{actor}` produced a non-finite float")));
    if actor == "neg" {
        return Ok(-operands[0]);
    }
    operands[1..].iter().try_fold(operands[0], |acc, &x| match actor {
        "add" => finite(acc + x),
        "sub" => finite(acc - x),
        "mul" => finite(acc * x),
        "div" | "mod" if x == 0.0 => Err(Fault::whole("divide_by_zero", format!("`{actor}` by zero"))),
        "div" => finite(acc / x),
        _ => finite(acc % x),
    })
}

/// Two numbers (mixed int/float compare as floats) or two strings.
fn ordering(actor: &str, args: &[Yaml]) -> Result<Yaml, Fault> {
    let order = match (&args[0], &args[1]) {
        (Yaml::String(a), Yaml::String(b)) => a.cmp(b),
        (Yaml::Integer(a), Yaml::Integer(b)) => a.cmp(b),
        _ => number(args, 0)?.float().partial_cmp(&number(args, 1)?.float()).unwrap_or(Ordering::Equal),
    };
    Ok(Yaml::Boolean(match actor {
        "lt" => order.is_lt(),
        "le" => order.is_le(),
        "gt" => order.is_gt(),
        _ => order.is_ge(),
    }))
}

/// `int`: floats truncate toward zero; strings must parse as integers.
fn to_int(value: &Yaml) -> Result<Yaml, Fault> {
    match value {
        Yaml::Integer(_) => Ok(value.clone()),
        Yaml::Real(_) => {
            let truncated = as_f64(value).unwrap_or(f64::NAN).trunc();
            if (i64::MIN as f64..-(i64::MIN as f64)).contains(&truncated) {
                Ok(Yaml::Integer(truncated as i64))
            } else {
                Err(Fault::at(0, "overflow", "float is outside the i64 range".to_string()))
            }
        }
        Yaml::String(s) => s.trim().parse().map(Yaml::Integer).map_err(|_| Fault::at(0, "conversion", format!("cannot parse `{s}` as an integer"))),
        other => Err(Fault::expected(0, "number or string", other)),
    }
}

/// `float`: integers widen; strings must parse as finite floats.
fn to_float(value: &Yaml) -> Result<Yaml, Fault> {
    match value {
        Yaml::Integer(i) => Ok(float_yaml(*i as f64)),
        Yaml::Real(_) => Ok(value.clone()),
        Yaml::String(s) => match s.trim().parse::<f64>() {
            Ok(f) if f.is_finite() => Ok(float_yaml(f)),
            _ => Err(Fault::at(0, "conversion", format!("cannot parse `{s}` as a float"))),
        },
        other => Err(Fault::expected(0, "number or string", other)),
    }
}
