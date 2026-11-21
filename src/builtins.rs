//! Builtin actors as pure functions over evaluated values. The evaluator owns evaluation order
//! (including registered forms); this module owns arity, typing and the operations.
//!
//! Type rules: there is no silent coercion except numeric promotion. `int` op `int` stays `int`;
//! if either operand is a `float` the result is a `float`. Ordering (`lt`..`ge`) compares two numbers
//! or two strings. `eq`/`ne` are structural, comparing numbers by value (`1` equals `1.0`).
//! Conversions between types are explicit: `str`, `int`, `float`.

use crate::value::Value;
use crate::model::PolicyCall;

mod collection;
mod data;
mod search;
mod fault;
mod number;
pub(crate) mod typing;
mod value;

pub use fault::Fault;
pub use typing::{is_primitive, primitives, refine, violations, Ask, Violation};
pub(crate) use typing::{describe, function_type, Field, Ty};
use typing::Pred;
use value::equal;
pub(crate) use value::type_name;

/// Authoritative dependency mapping shared by linking and execution.
pub(crate) fn policy_module(actor: &str) -> Option<&'static str> {
    match descriptor(actor)?.evaluation {
        Evaluation::Policy(module) => Some(module),
        _ => None,
    }
}

pub(crate) fn prepare_policy(actor: &str, args: &[Value], pred: &mut Pred) -> Result<Option<PolicyCall>, Fault> {
    if policy_module(actor).is_some() { typing::prepare(actor, args, pred) } else { Ok(None) }
}

/// Evaluator-owned forms. Exhaustive dispatch keeps registration and evaluation policy aligned.
#[derive(Clone, Copy, PartialEq, Eq)]
pub(crate) enum Form {
    Call, If, And, Or, Cond, Let, Fn, Map, Filter, Fold, Each, Until,
    Try, Finally, Refine, Import, TypeTime, Vm,
}

/// Evaluation policy; strict families retain their existing operation implementations.
#[derive(Clone, Copy)]
pub(crate) enum Evaluation {
    Search, Number, Collection, Typing, Data, Equal, NotEqual, Not, Text, Type, Assert, Raise,
    Form(Form),
    /// Eager arguments followed by a machine operation handled by the request dispatcher.
    Engine,
    /// Validated strict arguments followed by an ordinary embedded Helix function.
    Policy(&'static str),
}

/// The single registration authority for builtin identity, arity and evaluation policy.
pub(crate) struct Descriptor {
    pub(crate) name: &'static str,
    /// Parameter names, the source of `arity`: bracketed names are optional and a name ending
    /// in `...` stands for any number of further arguments.
    pub(crate) signature: &'static str,
    /// One written type per `signature` parameter, then `-> result`, in the notation of type
    /// annotations (`union(string, list)`, `any...` for a variadic tail). `number` abbreviates
    /// `union(integer, float)` and `type` stands for any type descriptor.
    pub(crate) types: &'static str,
    /// One line saying what the builtin returns or does.
    pub(crate) summary: &'static str,
    pub(crate) arity: (usize, Option<usize>),
    pub(crate) evaluation: Evaluation,
}

const fn actor(name: &'static str, signature: &'static str, types: &'static str, summary: &'static str, evaluation: Evaluation) -> Descriptor {
    Descriptor { name, signature, types, summary, arity: signature_arity(signature), evaluation }
}

/// `(minimum, maximum)` argument counts a signature admits. Parameters are separated by
/// commas outside brackets and braces; `[` opens the optional tail and `...` marks a variadic.
const fn signature_arity(signature: &str) -> (usize, Option<usize>) {
    let bytes = signature.as_bytes();
    let (mut min, mut max, mut variadic) = (0, 0, false);
    let (mut optional, mut opening, mut depth, mut i) = (false, false, 0, 0);
    let mut dots = false;
    while i <= bytes.len() {
        let byte = if i < bytes.len() { bytes[i] } else { b',' };
        match byte {
            b'{' => depth += 1,
            b'}' => depth -= 1,
            b'[' if depth == 0 && i + 1 < bytes.len() && bytes[i + 1] == b',' => opening = true,
            b'.' if depth == 0 => dots = true,
            b',' if depth == 0 => {
                if dots { variadic = true } else {
                    max += 1;
                    if !optional { min += 1 }
                }
                optional |= opening;
                dots = false;
            }
            _ => {}
        }
        i += 1;
    }
    (min, if variadic { None } else { Some(max) })
}

/// Comma-separated pieces outside braces and parentheses. Brackets do not nest here, so an
/// optional tail (`x[, y]`) splits like any other and `[params]` stays one piece.
fn split_top(text: &'static str) -> Vec<&'static str> {
    let (mut pieces, mut depth, mut start) = (Vec::new(), 0i32, 0);
    for (i, c) in text.char_indices() {
        match c {
            '{' | '(' => depth += 1,
            '}' | ')' => depth -= 1,
            ',' if depth == 0 => {
                pieces.push(&text[start..i]);
                start = i + 1;
            }
            _ => {}
        }
    }
    pieces.push(&text[start..]);
    pieces
}

const ACTORS: &[Descriptor] = &[
    actor("promise", "kind, capabilities", "string, list -> type", "a first-class promise type, such as `durable` with `[reversible]`", Evaluation::Typing),
    actor("mixin", "machine, promise_type", "vm, type -> vm", "an independent machine version with the promise attached", Evaluation::Typing),
    actor("vm", "function", "function -> vm", "a ready child machine for a zero-argument function, without running it", Evaluation::Form(Form::Vm)),
    actor("step", "machine", "vm -> vm", "the machine version after one engine transition", Evaluation::Engine),
    actor("inspect", "machine", "vm -> map", "read-only data describing a machine's status, location and state", Evaluation::Engine),
    actor("effect", "name, request", "string, any -> any", "request a host effect the caller authorized", Evaluation::Engine),
    actor("http_serve", "address, handler", "string, function -> any", "serve HTTP requests through a handler function (host capability)", Evaluation::Engine),
    actor("source_subtype", "left, right", "any, any -> boolean", "whether the bounded checker proves one source type below another", Evaluation::Search),
    actor("regex_find", "pattern, text", "string, string -> list", "regex matches as UTF-8 byte spans with capture text; prefer `std:text.regex`", Evaluation::Search),
    actor("source_parse", "source", "string -> any", "parse Helix source text into its normalized representation", Evaluation::Data),
    actor("json_parse", "text", "string -> any", "strict JSON text to a value", Evaluation::Data),
    actor("json_dump", "value", "any -> string", "a value to JSON text", Evaluation::Data),
    actor("csv_parse", "text", "string -> list", "strict CSV text to a list of rows", Evaluation::Data),
    actor("csv_dump", "rows", "list -> string", "a list of rows to CSV text", Evaluation::Data),
    actor("add", "a, b, more...", "number, number, number... -> number", "sum, folded left; int + int stays int, any float makes a float", Evaluation::Number),
    actor("mul", "a, b, more...", "number, number, number... -> number", "product, folded left", Evaluation::Number),
    actor("sub", "a, b", "number, number -> number", "a minus b", Evaluation::Number),
    actor("div", "a, b", "number, number -> number", "a divided by b; integer division truncates toward zero", Evaluation::Number),
    actor("mod", "a, b", "number, number -> number", "remainder of a / b, truncating toward zero", Evaluation::Number),
    actor("neg", "a", "number -> number", "negated number", Evaluation::Number),
    actor("eq", "a, b", "any, any -> boolean", "structural equality of any values (1 equals 1.0)", Evaluation::Equal),
    actor("ne", "a, b", "any, any -> boolean", "structural inequality of any values", Evaluation::NotEqual),
    actor("lt", "a, b", "union(number, string), union(number, string) -> boolean", "a < b for two numbers or two strings", Evaluation::Number),
    actor("le", "a, b", "union(number, string), union(number, string) -> boolean", "a <= b for two numbers or two strings", Evaluation::Number),
    actor("gt", "a, b", "union(number, string), union(number, string) -> boolean", "a > b for two numbers or two strings", Evaluation::Number),
    actor("ge", "a, b", "union(number, string), union(number, string) -> boolean", "a >= b for two numbers or two strings", Evaluation::Number),
    actor("not", "bool", "boolean -> boolean", "boolean negation", Evaluation::Not),
    actor("and", "a, b, more...", "boolean, boolean, boolean... -> boolean", "lazy boolean and: stops at the first false", Evaluation::Form(Form::And)),
    actor("or", "a, b, more...", "boolean, boolean, boolean... -> boolean", "lazy boolean or: stops at the first true", Evaluation::Form(Form::Or)),
    actor("if", "test, then, else", "boolean, any, any -> any", "evaluates only the branch the boolean test chooses", Evaluation::Form(Form::If)),
    actor("call", "function, args...", "function, any... -> any", "call a computed function value with arguments", Evaluation::Form(Form::Call)),
    actor("cond", "test, value, more..., default", "boolean, any, any..., any -> any", "value of the first true test (pairs), else the final default", Evaluation::Form(Form::Cond)),
    actor("let", "{name: value, ...}, body", "map, any -> any", "evaluate body with the map's bindings in scope", Evaluation::Form(Form::Let)),
    actor("fn", "[params], body[, result_type]", "list, any, type -> function", "a function; params are names or `[name, type]` pairs", Evaluation::Form(Form::Fn)),
    actor("map", "f, list", "function, list -> list", "list of f(item) for each item", Evaluation::Form(Form::Map)),
    actor("filter", "f, list", "function, list -> list", "items for which f(item) is true", Evaluation::Form(Form::Filter)),
    actor("fold", "f, init, list", "function, any, list -> any", "left fold: f(accumulator, item) from init", Evaluation::Form(Form::Fold)),
    actor("each", "f, list", "function, list -> null", "call f(item) for each item in order, for effects; returns null", Evaluation::Form(Form::Each)),
    actor("until", "done, step, init", "function, function, any -> any", "repeat state = step(state) until done(state); at most 100000 steps", Evaluation::Form(Form::Until)),
    actor("len", "x", "union(string, list, map) -> integer", "length of a string, list or map", Evaluation::Collection),
    actor("concat", "a, b, more...", "union(string, list), union(string, list), union(string, list)... -> union(string, list)", "join all strings, or all lists", Evaluation::Collection),
    actor("slice", "x, start, end", "union(string, list), integer, integer -> union(string, list)", "half-open [start, end) range of a string or list", Evaluation::Collection),
    actor("get", "x, key[, default]", "union(list, map), any, any -> any", "list index or map key; `missing` error without a default", Evaluation::Collection),
    actor("set", "x, key, value", "union(list, map), any, any -> union(list, map)", "copy with a list index replaced or a map key inserted/replaced", Evaluation::Collection),
    actor("append", "list, value, more...", "list, any, any... -> list", "copy of the list with values added at the end", Evaluation::Collection),
    actor("keys", "map", "map -> list", "list of a map's keys", Evaluation::Collection),
    actor("has", "x, item", "union(string, list, map), any -> boolean", "map key present, list element present, or substring present", Evaluation::Collection),
    actor("values", "map", "map -> list", "list of a map's values", Evaluation::Collection),
    actor("duplicates", "list", "list -> list", "members that occur more than once", Evaluation::Collection),
    actor("yaml_parse", "text", "string -> any", "strict YAML text to a value", Evaluation::Data),
    actor("yaml_dump", "value", "any -> string", "a value to YAML text", Evaluation::Data),
    actor("str", "scalar", "union(string, number, boolean, null) -> string", "text of a string, number, boolean or null", Evaluation::Text),
    actor("int", "x", "union(number, string) -> integer", "integer from a number (truncating) or numeric string", Evaluation::Number),
    actor("float", "x", "union(number, string) -> float", "float from a number or numeric string", Evaluation::Number),
    actor("type", "x", "any -> string", "type name: integer, float, string, boolean, null, list, map, function or vm", Evaluation::Type),
    actor("list_of", "item_type", "type -> type", "type of lists whose items fit item_type", Evaluation::Policy("type_descriptor")),
    actor("map_of", "value_type[, key_type]", "type, type -> type", "type of maps whose values (and keys) fit the given types", Evaluation::Policy("type_descriptor")),
    actor("record", "name_spec_pairs...", "any... -> type", "closed record type from alternating field names and specs", Evaluation::Policy("type_descriptor")),
    actor("function_of", "[param_types], result_type", "list, type -> type", "type of functions with these parameter and result types", Evaluation::Policy("type_descriptor")),
    actor("pure_function_of", "[param_types], result_type", "list, type -> type", "function type that must be provably free of effects", Evaluation::Policy("type_descriptor")),
    actor("optional", "type", "type -> type", "record field spec that may be absent", Evaluation::Policy("type_descriptor")),
    actor("default", "type, value", "type, any -> type", "record field spec filled with value when absent", Evaluation::Policy("type_descriptor")),
    actor("union", "type, more...", "type, type... -> type", "type of values fitting any member", Evaluation::Policy("type_descriptor")),
    actor("intersection", "type, more...", "type, type... -> type", "type of values fitting every member", Evaluation::Policy("type_descriptor")),
    actor("rec", "name, body", "string, type -> type", "recursive type; `var(name)` refers back to it", Evaluation::Policy("type_descriptor")),
    actor("var", "name", "string -> type", "reference to the enclosing `rec` type of that name", Evaluation::Policy("type_descriptor")),
    actor("tuple_of", "types...", "type... -> type", "type of lists with exactly one item per position", Evaluation::Policy("type_descriptor")),
    actor("enum", "value, more...", "any, any... -> type", "type admitting exactly the listed values", Evaluation::Policy("type_descriptor")),
    actor("literal", "value", "any -> type", "type admitting exactly one value", Evaluation::Policy("type_descriptor")),
    actor("refine", "type, predicate", "type, function -> type", "type narrowed by a boolean predicate function", Evaluation::Form(Form::Refine)),
    actor("with_field", "record, field, spec", "type, string, type -> type", "record type with a field added or replaced", Evaluation::Policy("type_descriptor")),
    actor("list", "items...", "any... -> list", "a list of the evaluated arguments", Evaluation::Collection),
    actor("dict", "key_value_pairs...", "any... -> map", "a map from alternating evaluated keys and values", Evaluation::Collection),
    actor("fields", "type", "type -> map", "map of a record or class's field specs", Evaluation::Typing),
    actor("is", "value, type", "any, type -> boolean", "whether value fits type", Evaluation::Typing),
    actor("violations", "value, type", "any, type -> list", "every nested violation of type by value", Evaluation::Typing),
    actor("expect", "value, type", "any, type -> any", "value if it fits type, else a structured type error", Evaluation::Typing),
    actor("subtype", "a, b", "type, type -> boolean", "whether type a is provably below type b", Evaluation::Typing),
    actor("equivalent", "a, b", "type, type -> boolean", "whether two types are provably mutual subtypes", Evaluation::Typing),
    actor("secret", "ref", "string -> any", "an opaque secret handle; secret-typed fields accept only these", Evaluation::Policy("type_descriptor")),
    actor("class", "name, record_type[, values]", "string, type, map -> type", "declare a class from a record type and fixed values", Evaluation::Typing),
    actor("extend", "name, parents, own_record, values", "string, list, type, map -> type", "child class with parents, its own fields and values", Evaluation::Typing),
    actor("inherit", "name, parents, values", "string, list, map -> type", "realized child class of parents with values", Evaluation::Typing),
    actor("realize", "class", "type -> type", "fill defaults, check values and constraints, mark concrete", Evaluation::Typing),
    actor("parents", "class", "type -> list", "immediate parent names in declaration order", Evaluation::Typing),
    actor("open_fields", "class", "type -> list", "required fields that still need values", Evaluation::Typing),
    actor("constrain", "class, predicate", "type, function -> type", "class with a whole-record predicate added", Evaluation::Typing),
    actor("drift", "class, actual", "type, map -> map", "`{missing, changed, extra}` between configured and observed values", Evaluation::Policy("convergence")),
    actor("converge", "class, actual", "type, map -> list", "ordered `set` steps that make actual match the class", Evaluation::Policy("convergence")),
    actor("type_time", "expression", "any -> any", "evaluate expression inside the pure type-time boundary", Evaluation::Form(Form::TypeTime)),
    actor("import", "path", "string -> any", "load a Helix document or `std:` library", Evaluation::Form(Form::Import)),
    actor("assert", "test[, message]", "boolean, string -> null", "null when test is true, else an `assertion` error", Evaluation::Assert),
    actor("raise", "kind_or_error[, message[, data]]", "union(string, map), string, any -> never", "fail with a chosen kind and message, or re-raise an error map", Evaluation::Raise),
    actor("try", "expr, handler", "any, function -> any", "expr, or handler(error_map) if expr fails", Evaluation::Form(Form::Try)),
    actor("finally", "expr, cleanup", "any, any -> any", "expr, then cleanup whether or not expr failed", Evaluation::Form(Form::Finally)),
];

/// Builtin families in the order `helix builtins` lists them.
pub(crate) const FAMILIES: &[&str] =
    &["forms", "arithmetic and comparison", "collections", "text and data", "types", "errors", "machines and effects"];

impl Descriptor {
    /// Parameter names from `signature`, one per position; optional and variadic markers dropped.
    pub(crate) fn parameter_names(&self) -> Vec<&'static str> {
        split_top(self.signature).into_iter().map(|piece| {
            let piece = piece.trim().trim_end_matches('[').trim_end_matches("...");
            if piece.contains('[') { piece } else { piece.trim_end_matches(']') }
        }).collect()
    }

    /// The written type of each parameter (a variadic tail keeps its `...`) and of the result.
    pub(crate) fn parameter_types(&self) -> (Vec<&'static str>, &'static str) {
        let (params, result) = self.types.rsplit_once(" -> ").unwrap_or((self.types, "any"));
        (split_top(params).into_iter().map(str::trim).collect(), result.trim())
    }

    /// The `(message, hint)` of an arity error when `count` arguments do not fit the signature.
    pub(crate) fn misfit(&self, count: usize) -> Option<(String, String)> {
        let (min, max) = self.arity;
        if count >= min && max.is_none_or(|max| count <= max) {
            return None;
        }
        let arguments = |n: usize| if n == 1 { "1 argument".to_string() } else { format!("{n} arguments") };
        let expected = match max {
            Some(max) if max == min => arguments(max),
            Some(max) => format!("{min} to {}", arguments(max)),
            None => format!("at least {}", arguments(min)),
        };
        Some((
            format!("`{}` takes {expected}, got {count}", self.name),
            format!("write `!call [{}, {}]`: {}", self.name, self.signature, self.summary),
        ))
    }

    /// The reader-facing family of this builtin, derived from its evaluation policy.
    pub(crate) fn family(&self) -> &'static str {
        match self.evaluation {
            Evaluation::Form(Form::Try | Form::Finally) | Evaluation::Assert | Evaluation::Raise => "errors",
            Evaluation::Form(Form::Refine | Form::TypeTime) => "types",
            Evaluation::Form(Form::Vm) => "machines and effects",
            Evaluation::Form(_) => "forms",
            Evaluation::Number | Evaluation::Equal | Evaluation::NotEqual | Evaluation::Not => "arithmetic and comparison",
            Evaluation::Collection => "collections",
            Evaluation::Text | Evaluation::Data | Evaluation::Search => "text and data",
            Evaluation::Type | Evaluation::Typing | Evaluation::Policy("type_descriptor") => "types",
            Evaluation::Engine | Evaluation::Policy(_) => "machines and effects",
        }
    }
}

/// The builtins of one family, in registration order.
pub(crate) fn members(family: &str) -> impl Iterator<Item = &'static Descriptor> + '_ {
    ACTORS.iter().filter(move |actor| actor.family() == family)
}

pub(crate) fn descriptor(name: &str) -> Option<&'static Descriptor> {
    ACTORS.iter().find(|actor| actor.name == name)
}

/// Every builtin actor name, in registration order.
pub(crate) fn names() -> impl Iterator<Item = &'static str> {
    ACTORS.iter().map(|actor| actor.name)
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
        Evaluation::Search => search::apply(actor.name, args),
        Evaluation::Data => data::apply(actor.name, &args[0]),
        Evaluation::Text => text(&args[0]),
        Evaluation::Type => Ok(Value::String(type_name(&args[0]).to_string())),
        Evaluation::Assert => assert(args),
        Evaluation::Raise => Err(raise(args)),
        Evaluation::Form(_) | Evaluation::Engine | Evaluation::Policy(_) => unreachable!("engine operations are dispatched by the evaluator"),
    }
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
        Ok((kind, message, data)) => Fault { hint: None, kind, message, arg: None, data, cause: if args.len() == 1 { args.first().cloned() } else { None } },
        Err(fault) => fault,
    }
}

#[cfg(test)]
mod tests {
    use super::signature_arity;

    #[test]
    fn signatures_determine_arity() {
        for (signature, arity) in [
            ("a, b", (2, Some(2))),
            ("x, key[, default]", (2, Some(3))),
            ("kind_or_error[, message[, data]]", (1, Some(3))),
            ("a, b, more...", (2, None)),
            ("items...", (0, None)),
            ("test, value, more..., default", (3, None)),
            ("{name: value, ...}, body", (2, Some(2))),
            ("[params], body[, result_type]", (2, Some(3))),
        ] {
            assert_eq!(signature_arity(signature), arity, "{signature}");
        }
    }

    #[test]
    fn every_builtin_types_each_parameter() {
        for d in super::ACTORS {
            let (types, _) = d.parameter_types();
            assert_eq!(d.parameter_names().len(), types.len(), "`{}`: signature `{}` vs types `{}`", d.name, d.signature, d.types);
            assert!(d.types.contains(" -> "), "`{}` types need a result", d.name);
        }
    }
}
