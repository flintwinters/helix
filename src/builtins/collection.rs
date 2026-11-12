//! Strings, lists and maps. All operations are pure: they return a new value.
//! Strings index by character; maps have string keys and keep insertion order.

use crate::value::Value;

use super::fault::Fault;
use super::value::equal;

pub(super) fn apply(actor: &str, args: &[Value]) -> Result<Value, Fault> {
    match actor {
        "list" => Ok(Value::Array((args.to_vec()).into())),
        "dict" => dict(args),
        "len" => len(&args[0]),
        "concat" => concat(args),
        "slice" => slice(args),
        "get" => get(args),
        "set" => set(args),
        "append" => append(args),
        "keys" => keys(&args[0]),
        "values" => values(&args[0]),
        "duplicates" => duplicates(&args[0]),
        "has" => has(args),
        _ => unreachable!("`{actor}` is not a collection builtin"),
    }
}

/// A non-negative position as `usize`.
fn index(args: &[Value], at: usize) -> Result<usize, Fault> {
    match &args[at] {
        Value::Integer(i) => usize::try_from(*i).map_err(|_| Fault::at(at, "range", format!("index {i} is negative"))),
        other => Err(Fault::expected(at, "integer", other)),
    }
}

fn key(args: &[Value], at: usize) -> Result<Value, Fault> {
    match &args[at] {
        key @ Value::String(_) => Ok(key.clone()),
        other => Err(Fault::expected(at, "string key", other)),
    }
}

fn len(value: &Value) -> Result<Value, Fault> {
    let n = match value {
        Value::String(s) => s.chars().count(),
        Value::Array(v) => v.len(),
        Value::Hash(h) => h.len(),
        other => return Err(Fault::expected(0, "string, list or map", other)),
    };
    Ok(Value::Integer(n as i64))
}

/// All arguments must be strings, or all lists.
fn concat(args: &[Value]) -> Result<Value, Fault> {
    match &args[0] {
        Value::String(_) => {
            let mut out = String::new();
            for (i, arg) in args.iter().enumerate() {
                match arg {
                    Value::String(s) => out.push_str(s),
                    other => return Err(Fault::expected(i, "string", other)),
                }
            }
            Ok(Value::String(out))
        }
        Value::Array(_) => {
            let mut out = Vec::new();
            for (i, arg) in args.iter().enumerate() {
                match arg {
                    Value::Array(v) => out.extend(v.iter().cloned()),
                    other => return Err(Fault::expected(i, "list", other)),
                }
            }
            Ok(Value::Array(out.into()))
        }
        other => Err(Fault::expected(0, "string or list", other)),
    }
}

/// `[slice, x, start, end]`: the half-open range `start..end`.
fn slice(args: &[Value]) -> Result<Value, Fault> {
    let bounds = |len: usize| {
        let (start, end) = (index(args, 1)?, index(args, 2)?);
        if start <= end && end <= len {
            Ok((start, end))
        } else {
            Err(Fault::at(if end > len { 2 } else { 1 }, "range", format!("slice {start}..{end} is out of range for length {len}")))
        }
    };
    match &args[0] {
        Value::String(s) => {
            let chars: Vec<char> = s.chars().collect();
            let (start, end) = bounds(chars.len())?;
            Ok(Value::String(chars[start..end].iter().collect()))
        }
        Value::Array(v) => {
            let (start, end) = bounds(v.len())?;
            Ok(Value::Array((v[start..end].to_vec()).into()))
        }
        other => Err(Fault::expected(0, "string or list", other)),
    }
}

/// `[get, x, key]` or `[get, x, key, default]`; without a default a missing entry is a `missing` error.
fn get(args: &[Value]) -> Result<Value, Fault> {
    let found = match &args[0] {
        Value::Array(v) => v.get(index(args, 1)?),
        Value::Hash(h) => h.get(&key(args, 1)?),
        other => return Err(Fault::expected(0, "list or map", other)),
    };
    match (found, args.get(2)) {
        (Some(value), _) => Ok(value.clone()),
        (None, Some(default)) => Ok(default.clone()),
        (None, None) => Err(Fault::at(1, "missing", match &args[1] {
            Value::String(k) => format!("no key `{k}`"),
            Value::Integer(i) => format!("no index {i}"),
            _ => "no such entry".to_string(),
        })),
    }
}

/// `[set, x, key, value]`: replaces an existing list index or inserts/replaces a map key.
fn set(args: &[Value]) -> Result<Value, Fault> {
    match &args[0] {
        Value::Array(v) => {
            let at = index(args, 1)?;
            let mut out = v.clone();
            *out.get_mut(at).ok_or_else(|| Fault::at(1, "range", format!("index {at} is out of range for length {}", v.len())))? = args[2].clone();
            Ok(Value::Array(out.into()))
        }
        Value::Hash(h) => {
            // Replacing an existing key keeps its position; `insert` alone would move it to the end.
            let mut out = h.clone();
            let k = key(args, 1)?;
            match out.get_mut(&k) {
                Some(slot) => *slot = args[2].clone(),
                None => drop(out.insert(k, args[2].clone())),
            }
            Ok(Value::Hash(out))
        }
        other => Err(Fault::expected(0, "list or map", other)),
    }
}

fn append(args: &[Value]) -> Result<Value, Fault> {
    match &args[0] {
        Value::Array(v) => Ok(Value::Array(v.iter().chain(&args[1..]).cloned().collect())),
        other => Err(Fault::expected(0, "list", other)),
    }
}

fn keys(value: &Value) -> Result<Value, Fault> {
    match value {
        Value::Hash(h) => Ok(Value::Array(h.keys().cloned().collect())),
        other => Err(Fault::expected(0, "map", other)),
    }
}

/// Map: key present. List: element present (by `eq`). String: substring present.
fn has(args: &[Value]) -> Result<Value, Fault> {
    let found = match (&args[0], &args[1]) {
        (Value::Hash(h), key @ Value::String(_)) => h.contains_key(key),
        (Value::Hash(_), other) => return Err(Fault::expected(1, "string key", other)),
        (Value::Array(v), needle) => v.iter().any(|element| equal(element, needle)),
        (Value::String(s), Value::String(needle)) => s.contains(needle.as_str()),
        (Value::String(_), other) => return Err(Fault::expected(1, "string", other)),
        (other, _) => return Err(Fault::expected(0, "string, list or map", other)),
    };
    Ok(Value::Boolean(found))
}

fn values(value: &Value) -> Result<Value, Fault> {
    match value {
        Value::Hash(h) => Ok(Value::Array(h.values().cloned().collect())),
        other => Err(Fault::expected(0, "map", other)),
    }
}

/// Each element that occurs more than once (by `eq`), listed once, in order of first repetition.
fn duplicates(value: &Value) -> Result<Value, Fault> {
    let Value::Array(items) = value else { return Err(Fault::expected(0, "list", value)) };
    let mut repeated: Vec<Value> = Vec::new();
    for (i, item) in items.iter().enumerate() {
        if items[..i].iter().any(|earlier| equal(earlier, item)) && !repeated.iter().any(|seen| equal(seen, item)) {
            repeated.push(item.clone());
        }
    }
    Ok(Value::Array(repeated.into()))
}

/// `[dict, key, value, key, value, ...]`: a map whose keys and values are evaluated expressions.
fn dict(args: &[Value]) -> Result<Value, Fault> {
    if args.len() % 2 != 0 {
        return Err(Fault::whole("arity", "`dict` takes alternating keys and values".into()));
    }
    let mut out = crate::value::Hash::new();
    for (i, pair) in args.chunks_exact(2).enumerate() {
        let key = key(args, 2 * i)?;
        out.insert(key, pair[1].clone());
    }
    Ok(Value::Hash(out))
}
