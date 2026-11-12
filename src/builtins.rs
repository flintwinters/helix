//! Builtin actors as pure functions over evaluated values. The evaluator owns evaluation order
//! (including registered forms); this module owns arity, typing and the operations.
//!
//! Type rules: there is no silent coercion except numeric promotion. `int` op `int` stays `int`;
//! if either operand is a `float` the result is a `float`. Ordering (`lt`..`ge`) compares two numbers
//! or two strings. `eq`/`ne` are structural, comparing numbers by value (`1` equals `1.0`).
//! Conversions between types are explicit: `str`, `int`, `float`.

use crate::value::Value;

mod collection;
mod data;
mod fault;
mod number;
mod typing;
mod value;

pub use fault::Fault;
pub use typing::{is_primitive, refine, violations, Violation};
use typing::Pred;
use value::{equal, type_name};

/// Evaluator-owned forms. Exhaustive dispatch keeps registration and evaluation policy aligned.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum Form {
    Call, Closure, If, And, Or, Cond, Let, Fn, Map, Filter, Fold, Each, Until,
    Try, Finally, Refine, Import, TypeTime, Vm,
}

/// Evaluation policy; strict families retain their existing operation implementations.
#[derive(Clone, Copy)]
pub(crate) enum Evaluation {
    Number, Collection, Typing, Data, Equal, NotEqual, Not, Text, Type, Assert, Raise,
    Form(Form),
    /// Eager arguments followed by a machine operation handled by the request dispatcher.
    Engine,
}

/// The single registration authority for builtin identity, arity and evaluation policy.
pub(crate) struct Descriptor {
    pub(crate) name: &'static str,
    pub(crate) arity: (usize, Option<usize>),
    pub(crate) evaluation: Evaluation,
}

const fn actor(name: &'static str, min: usize, max: Option<usize>, evaluation: Evaluation) -> Descriptor {
    Descriptor { name, arity: (min, max), evaluation }
}

const ACTORS: &[Descriptor] = &[
    actor("vm", 1, Some(1), Evaluation::Form(Form::Vm)),
    actor("step", 1, Some(1), Evaluation::Engine),
    actor("inspect", 1, Some(1), Evaluation::Engine),
    actor("effect", 2, Some(2), Evaluation::Engine),
    actor("json_parse", 1, Some(1), Evaluation::Data),
    actor("json_dump", 1, Some(1), Evaluation::Data),
    actor("csv_parse", 1, Some(1), Evaluation::Data),
    actor("csv_dump", 1, Some(1), Evaluation::Data),
    actor("add", 2, None, Evaluation::Number),
    actor("mul", 2, None, Evaluation::Number),
    actor("sub", 2, Some(2), Evaluation::Number),
    actor("div", 2, Some(2), Evaluation::Number),
    actor("mod", 2, Some(2), Evaluation::Number),
    actor("neg", 1, Some(1), Evaluation::Number),
    actor("eq", 2, Some(2), Evaluation::Equal),
    actor("ne", 2, Some(2), Evaluation::NotEqual),
    actor("lt", 2, Some(2), Evaluation::Number),
    actor("le", 2, Some(2), Evaluation::Number),
    actor("gt", 2, Some(2), Evaluation::Number),
    actor("ge", 2, Some(2), Evaluation::Number),
    actor("not", 1, Some(1), Evaluation::Not),
    actor("and", 2, None, Evaluation::Form(Form::And)),
    actor("or", 2, None, Evaluation::Form(Form::Or)),
    actor("if", 3, Some(3), Evaluation::Form(Form::If)),
    actor("call", 1, None, Evaluation::Form(Form::Call)),
    actor("closure", 2, Some(2), Evaluation::Form(Form::Closure)),
    actor("cond", 3, None, Evaluation::Form(Form::Cond)),
    actor("let", 2, Some(2), Evaluation::Form(Form::Let)),
    actor("fn", 2, Some(3), Evaluation::Form(Form::Fn)),
    actor("map", 2, Some(2), Evaluation::Form(Form::Map)),
    actor("filter", 2, Some(2), Evaluation::Form(Form::Filter)),
    actor("fold", 3, Some(3), Evaluation::Form(Form::Fold)),
    actor("each", 2, Some(2), Evaluation::Form(Form::Each)),
    actor("until", 3, Some(3), Evaluation::Form(Form::Until)),
    actor("len", 1, Some(1), Evaluation::Collection),
    actor("concat", 2, None, Evaluation::Collection),
    actor("slice", 3, Some(3), Evaluation::Collection),
    actor("get", 2, Some(3), Evaluation::Collection),
    actor("set", 3, Some(3), Evaluation::Collection),
    actor("append", 2, None, Evaluation::Collection),
    actor("keys", 1, Some(1), Evaluation::Collection),
    actor("has", 2, Some(2), Evaluation::Collection),
    actor("values", 1, Some(1), Evaluation::Collection),
    actor("duplicates", 1, Some(1), Evaluation::Collection),
    actor("yaml_parse", 1, Some(1), Evaluation::Data),
    actor("yaml_dump", 1, Some(1), Evaluation::Data),
    actor("str", 1, Some(1), Evaluation::Text),
    actor("int", 1, Some(1), Evaluation::Number),
    actor("float", 1, Some(1), Evaluation::Number),
    actor("type", 1, Some(1), Evaluation::Type),
    actor("list_of", 1, Some(1), Evaluation::Typing),
    actor("map_of", 1, Some(1), Evaluation::Typing),
    actor("record", 0, None, Evaluation::Typing),
    actor("optional", 1, Some(1), Evaluation::Typing),
    actor("default", 2, Some(2), Evaluation::Typing),
    actor("union", 1, None, Evaluation::Typing),
    actor("enum", 1, None, Evaluation::Typing),
    actor("literal", 1, Some(1), Evaluation::Typing),
    actor("refine", 2, Some(2), Evaluation::Form(Form::Refine)),
    actor("with_field", 3, Some(3), Evaluation::Typing),
    actor("list", 0, None, Evaluation::Collection),
    actor("dict", 0, None, Evaluation::Collection),
    actor("fields", 1, Some(1), Evaluation::Typing),
    actor("is", 2, Some(2), Evaluation::Typing),
    actor("violations", 2, Some(2), Evaluation::Typing),
    actor("expect", 2, Some(2), Evaluation::Typing),
    actor("subtype", 2, Some(2), Evaluation::Typing),
    actor("secret", 1, Some(1), Evaluation::Typing),
    actor("class", 2, Some(3), Evaluation::Typing),
    actor("extend", 4, Some(4), Evaluation::Typing),
    actor("inherit", 3, Some(3), Evaluation::Typing),
    actor("realize", 1, Some(1), Evaluation::Typing),
    actor("parents", 1, Some(1), Evaluation::Typing),
    actor("open_fields", 1, Some(1), Evaluation::Typing),
    actor("constrain", 2, Some(2), Evaluation::Typing),
    actor("drift", 2, Some(2), Evaluation::Typing),
    actor("converge", 2, Some(2), Evaluation::Typing),
    actor("type_time", 1, Some(1), Evaluation::Form(Form::TypeTime)),
    actor("import", 1, Some(1), Evaluation::Form(Form::Import)),
    actor("assert", 1, Some(2), Evaluation::Assert),
    actor("raise", 1, Some(3), Evaluation::Raise),
    actor("try", 2, Some(2), Evaluation::Form(Form::Try)),
    actor("finally", 2, Some(2), Evaluation::Form(Form::Finally)),
];

pub(crate) fn descriptor(name: &str) -> Option<&'static Descriptor> {
    ACTORS.iter().find(|actor| actor.name == name)
}

/// `(minimum, maximum)` argument counts; `None` overall means not a builtin.
pub fn arity(name: &str) -> Option<(usize, Option<usize>)> {
    descriptor(name).map(|actor| actor.arity)
}

/// Apply a strict (eagerly evaluated) builtin with its registered operation family.
pub fn apply(name: &str, args: &[Value], pred: &mut Pred) -> Result<Value, Fault> {
    let actor = descriptor(name).expect("registered builtin");
    match actor.evaluation {
        Evaluation::Number => number::apply(actor.name, args),
        Evaluation::Equal => Ok(Value::Boolean(equal(&args[0], &args[1]))),
        Evaluation::NotEqual => Ok(Value::Boolean(!equal(&args[0], &args[1]))),
        Evaluation::Not => boolean(&args[0], 0).map(|b| Value::Boolean(!b)),
        Evaluation::Typing => typing::apply(actor.name, args, pred),
        Evaluation::Collection => collection::apply(actor.name, args),
        Evaluation::Data => data::apply(actor.name, &args[0]),
        Evaluation::Text => text(&args[0]),
        Evaluation::Type => Ok(Value::String(type_name(&args[0]).to_string())),
        Evaluation::Assert => assert(args),
        Evaluation::Raise => Err(raise(args)),
        Evaluation::Form(_) | Evaluation::Engine => unreachable!("engine operations are dispatched by the evaluator"),
    }
}

/// The name `type` reports for a value.
pub fn type_of(value: &Value) -> &'static str {
    type_name(value)
}

/// A boolean operand at argument `arg`: `not`, and every condition the evaluator's forms test.
pub fn boolean(value: &Value, arg: usize) -> Result<bool, Fault> {
    match value {
        Value::Boolean(b) => Ok(*b),
        other => Err(Fault::expected(arg, "boolean", other)),
    }
}

/// A list operand at argument `arg`, as used by the iteration forms.
pub fn list(value: Value, arg: usize) -> Result<Vec<Value>, Fault> {
    match value {
        Value::Array(items) => Ok(items.into_iter().collect()),
        other => Err(Fault::expected(arg, "list", &other)),
    }
}

/// `str`: scalar to its text; collections have no canonical text and are rejected.
fn text(value: &Value) -> Result<Value, Fault> {
    let rendered = match value {
        Value::String(s) => s.clone(),
        Value::Integer(i) => i.to_string(),
        Value::Float(r) => format!("{r:?}"),
        Value::Boolean(b) => b.to_string(),
        Value::Null => "null".to_string(),
        other => return Err(Fault::expected(0, "a scalar", other)),
    };
    Ok(Value::String(rendered))
}

/// `assert`: `null` when the condition holds, else an `assertion` fault carrying the message.
fn assert(args: &[Value]) -> Result<Value, Fault> {
    if boolean(&args[0], 0)? {
        return Ok(Value::Null);
    }
    match args.get(1) {
        None => Err(Fault::whole("assertion", "assertion failed".into())),
        Some(Value::String(message)) => Err(Fault::whole("assertion", message.clone())),
        Some(other) => Err(Fault::expected(1, "string message", other)),
    }
}

/// `raise`: always a fault. `[raise, kind, message]` / `[raise, kind, message, data]` choose their
/// own `kind`; `[raise, error_map]` re-raises a caught error (its `kind`, `message`, optional `data`).
fn raise(args: &[Value]) -> Fault {
    let text = |value: &Value, arg: usize, what: &str| match value {
        Value::String(s) => Ok(s.clone()),
        other => Err(Fault::expected(arg, what, other)),
    };
    let built = match args {
        [Value::Hash(error)] => {
            let field = |name: &str| error.get(&Value::String(name.into()));
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
