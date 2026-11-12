//! Builtin actors as pure functions over evaluated values. The evaluator owns evaluation order
//! (including the forms listed by `is_form`); this module owns arity, typing and the operations.
//!
//! Type rules: there is no silent coercion except numeric promotion. `int` op `int` stays `int`;
//! if either operand is a `float` the result is a `float`. Ordering (`lt`..`ge`) compares two numbers
//! or two strings. `eq`/`ne` are structural, comparing numbers by value (`1` equals `1.0`).
//! Conversions between types are explicit: `str`, `int`, `float`.

use yaml_rust2::Yaml;

mod collection;
mod fault;
mod number;
mod typing;
mod value;
mod yaml;

pub use fault::Fault;
pub use typing::{is_primitive, refine, violations, Violation};
use typing::Pred;
pub use value::is_function;
use value::{equal, type_name};

/// `(name, minimum, maximum)` argument counts; a `None` maximum is unbounded.
const ARITIES: &[(&str, usize, Option<usize>)] = &[
    ("add", 2, None), ("mul", 2, None), ("sub", 2, Some(2)), ("div", 2, Some(2)), ("mod", 2, Some(2)), ("neg", 1, Some(1)),
    ("eq", 2, Some(2)), ("ne", 2, Some(2)), ("lt", 2, Some(2)), ("le", 2, Some(2)), ("gt", 2, Some(2)), ("ge", 2, Some(2)),
    ("not", 1, Some(1)), ("and", 2, None), ("or", 2, None), ("if", 3, Some(3)),
    ("call", 1, None), ("closure", 2, Some(2)), ("cond", 3, None), ("let", 2, Some(2)), ("fn", 2, Some(3)),
    ("map", 2, Some(2)), ("filter", 2, Some(2)), ("fold", 3, Some(3)), ("each", 2, Some(2)), ("until", 3, Some(3)),
    ("len", 1, Some(1)), ("concat", 2, None), ("slice", 3, Some(3)), ("get", 2, Some(3)), ("set", 3, Some(3)),
    ("append", 2, None), ("keys", 1, Some(1)), ("has", 2, Some(2)), ("values", 1, Some(1)), ("duplicates", 1, Some(1)),
    ("yaml_parse", 1, Some(1)), ("yaml_dump", 1, Some(1)),
    ("str", 1, Some(1)), ("int", 1, Some(1)), ("float", 1, Some(1)), ("type", 1, Some(1)),
    ("list_of", 1, Some(1)), ("map_of", 1, Some(1)), ("record", 0, None), ("optional", 1, Some(1)), ("default", 2, Some(2)), ("union", 1, None),
    ("enum", 1, None), ("literal", 1, Some(1)), ("refine", 2, Some(2)), ("with_field", 3, Some(3)), ("list", 0, None), ("dict", 0, None), ("fields", 1, Some(1)), ("is", 2, Some(2)),
    ("violations", 2, Some(2)), ("expect", 2, Some(2)), ("subtype", 2, Some(2)), ("secret", 1, Some(1)),
    ("class", 2, Some(3)), ("extend", 4, Some(4)), ("inherit", 3, Some(3)), ("realize", 1, Some(1)), ("parents", 1, Some(1)), ("open_fields", 1, Some(1)),
    ("drift", 2, Some(2)), ("converge", 2, Some(2)),
    ("type_time", 1, Some(1)), ("import", 1, Some(1)), ("assert", 1, Some(2)), ("raise", 1, Some(3)), ("try", 2, Some(2)), ("finally", 2, Some(2)),
];

/// `(minimum, maximum)` argument counts; `None` overall means not a builtin.
pub fn arity(actor: &str) -> Option<(usize, Option<usize>)> {
    ARITIES.iter().find(|(name, ..)| *name == actor).map(|&(_, min, max)| (min, max))
}

/// Forms decide which arguments to evaluate (or call user functions), so the evaluator drives them.
pub fn is_form(actor: &str) -> bool {
    matches!(actor, "call" | "closure" | "if" | "and" | "or" | "cond" | "let" | "fn" | "map" | "filter" | "fold" | "each" | "until" | "try" | "finally" | "refine" | "import" | "type_time")
}

/// Apply a strict (eagerly evaluated) builtin.
pub fn apply(actor: &str, args: &[Yaml], pred: &mut Pred) -> Result<Yaml, Fault> {
    match actor {
        "add" | "sub" | "mul" | "div" | "mod" | "neg" | "lt" | "le" | "gt" | "ge" | "int" | "float" => number::apply(actor, args),
        "eq" => Ok(Yaml::Boolean(equal(&args[0], &args[1]))),
        "ne" => Ok(Yaml::Boolean(!equal(&args[0], &args[1]))),
        "not" => boolean(&args[0], 0).map(|b| Yaml::Boolean(!b)),
        _ if typing::handles(actor) => typing::apply(actor, args, pred),
        "len" | "list" | "dict" | "concat" | "slice" | "get" | "set" | "append" | "keys" | "has" | "values" | "duplicates" => collection::apply(actor, args),
        "yaml_parse" | "yaml_dump" => yaml::apply(actor, &args[0]),
        "str" => text(&args[0]),
        "type" => Ok(Yaml::String(type_name(&args[0]).to_string())),
        "assert" => assert(args),
        "raise" => Err(raise(args)),
        _ => unreachable!("`{actor}` is not a strict builtin"),
    }
}

/// The name `type` reports for a value.
pub fn type_of(value: &Yaml) -> &'static str {
    type_name(value)
}

/// A boolean operand at argument `arg`: `not`, and every condition the evaluator's forms test.
pub fn boolean(value: &Yaml, arg: usize) -> Result<bool, Fault> {
    match value {
        Yaml::Boolean(b) => Ok(*b),
        other => Err(Fault::expected(arg, "boolean", other)),
    }
}

/// A list operand at argument `arg`, as used by the iteration forms.
pub fn list(value: Yaml, arg: usize) -> Result<Vec<Yaml>, Fault> {
    match value {
        Yaml::Array(items) => Ok(items),
        other => Err(Fault::expected(arg, "list", &other)),
    }
}

/// `str`: scalar to its text; collections have no canonical text and are rejected.
fn text(value: &Yaml) -> Result<Yaml, Fault> {
    let rendered = match value {
        Yaml::String(s) => s.clone(),
        Yaml::Integer(i) => i.to_string(),
        Yaml::Real(r) => r.clone(),
        Yaml::Boolean(b) => b.to_string(),
        Yaml::Null => "null".to_string(),
        other => return Err(Fault::expected(0, "a scalar", other)),
    };
    Ok(Yaml::String(rendered))
}

/// `assert`: `null` when the condition holds, else an `assertion` fault carrying the message.
fn assert(args: &[Yaml]) -> Result<Yaml, Fault> {
    if boolean(&args[0], 0)? {
        return Ok(Yaml::Null);
    }
    match args.get(1) {
        None => Err(Fault::whole("assertion", "assertion failed".into())),
        Some(Yaml::String(message)) => Err(Fault::whole("assertion", message.clone())),
        Some(other) => Err(Fault::expected(1, "string message", other)),
    }
}

/// `raise`: always a fault. `[raise, kind, message]` / `[raise, kind, message, data]` choose their
/// own `kind`; `[raise, error_map]` re-raises a caught error (its `kind`, `message`, optional `data`).
fn raise(args: &[Yaml]) -> Fault {
    let text = |value: &Yaml, arg: usize, what: &str| match value {
        Yaml::String(s) => Ok(s.clone()),
        other => Err(Fault::expected(arg, what, other)),
    };
    let built = match args {
        [Yaml::Hash(error)] => {
            let field = |name: &str| error.get(&Yaml::String(name.into()));
            match (field("kind"), field("message")) {
                (Some(kind), Some(message)) => text(kind, 0, "string `kind`").and_then(|k| Ok((k, text(message, 0, "string `message`")?, field("data").cloned()))),
                _ => Err(Fault::at(0, "type", "an error map needs `kind` and `message`".into())),
            }
        }
        [other] => Err(Fault::expected(0, "an error map, or a kind and a message", other)),
        [kind, message, rest @ ..] => text(kind, 0, "string kind").and_then(|k| Ok((k, text(message, 1, "string message")?, rest.first().cloned()))),
        [] => unreachable!("`raise` takes at least 1 argument"),
    };
    match built {
        Ok((kind, _, _)) if kind.is_empty() => Fault::at(0, "type", "an error `kind` must not be empty".into()),
        Ok((kind, message, data)) => Fault { kind, message, arg: None, data, cause: if args.len() == 1 { args.first().cloned() } else { None } },
        Err(fault) => fault,
    }
}
