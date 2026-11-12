//! Strings, lists and maps. All operations are pure: they return a new value.
//! Strings index by character; maps have string keys and keep insertion order.

use yaml_rust2::Yaml;

use super::fault::Fault;
use crate::value::equal;

pub(super) fn apply(actor: &str, args: &[Yaml]) -> Result<Yaml, Fault> {
    match actor {
        "list" => Ok(Yaml::Array(args.to_vec())),
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
fn index(args: &[Yaml], at: usize) -> Result<usize, Fault> {
    match &args[at] {
        Yaml::Integer(i) => usize::try_from(*i).map_err(|_| Fault::at(at, "range", format!("index {i} is negative"))),
        other => Err(Fault::expected(at, "integer", other)),
    }
}

fn key(args: &[Yaml], at: usize) -> Result<&Yaml, Fault> {
    match &args[at] {
        key @ Yaml::String(_) => Ok(key),
        other => Err(Fault::expected(at, "string key", other)),
    }
}

fn len(value: &Yaml) -> Result<Yaml, Fault> {
    let n = match value {
        Yaml::String(s) => s.chars().count(),
        Yaml::Array(v) => v.len(),
        Yaml::Hash(h) => h.len(),
        other => return Err(Fault::expected(0, "string, list or map", other)),
    };
    Ok(Yaml::Integer(n as i64))
}

/// All arguments must be strings, or all lists.
fn concat(args: &[Yaml]) -> Result<Yaml, Fault> {
    match &args[0] {
        Yaml::String(_) => {
            let mut out = String::new();
            for (i, arg) in args.iter().enumerate() {
                match arg {
                    Yaml::String(s) => out.push_str(s),
                    other => return Err(Fault::expected(i, "string", other)),
                }
            }
            Ok(Yaml::String(out))
        }
        Yaml::Array(_) => {
            let mut out = Vec::new();
            for (i, arg) in args.iter().enumerate() {
                match arg {
                    Yaml::Array(v) => out.extend(v.iter().cloned()),
                    other => return Err(Fault::expected(i, "list", other)),
                }
            }
            Ok(Yaml::Array(out))
        }
        other => Err(Fault::expected(0, "string or list", other)),
    }
}

/// `[slice, x, start, end]`: the half-open range `start..end`.
fn slice(args: &[Yaml]) -> Result<Yaml, Fault> {
    let bounds = |len: usize| {
        let (start, end) = (index(args, 1)?, index(args, 2)?);
        if start <= end && end <= len {
            Ok((start, end))
        } else {
            Err(Fault::at(if end > len { 2 } else { 1 }, "range", format!("slice {start}..{end} is out of range for length {len}")))
        }
    };
    match &args[0] {
        Yaml::String(s) => {
            if s.is_ascii() {
                let (start, end) = bounds(s.len())?;
                return Ok(Yaml::String(s[start..end].to_owned()));
            }
            let (start, end) = bounds(s.chars().count())?;
            let mut offsets = s.char_indices().map(|(byte, _)| byte).chain(std::iter::once(s.len()));
            let first = offsets.nth(start).expect("validated character boundary");
            let last = if start == end { first } else {
                offsets.nth(end - start - 1).expect("validated character boundary")
            };
            Ok(Yaml::String(s[first..last].to_owned()))
        }
        Yaml::Array(v) => {
            let (start, end) = bounds(v.len())?;
            Ok(Yaml::Array(v[start..end].to_vec()))
        }
        other => Err(Fault::expected(0, "string or list", other)),
    }
}

/// `[get, x, key]` or `[get, x, key, default]`; without a default a missing entry is a `missing` error.
fn get(args: &[Yaml]) -> Result<Yaml, Fault> {
    let found = match &args[0] {
        Yaml::Array(v) => v.get(index(args, 1)?),
        Yaml::Hash(h) => h.get(key(args, 1)?),
        other => return Err(Fault::expected(0, "list or map", other)),
    };
    match (found, args.get(2)) {
        (Some(value), _) => Ok(value.clone()),
        (None, Some(default)) => Ok(default.clone()),
        (None, None) => Err(Fault::at(1, "missing", match &args[1] {
            Yaml::String(k) => format!("no key `{k}`"),
            Yaml::Integer(i) => format!("no index {i}"),
            _ => "no such entry".to_string(),
        })),
    }
}

/// `[set, x, key, value]`: replaces an existing list index or inserts/replaces a map key.
fn set(args: &[Yaml]) -> Result<Yaml, Fault> {
    match &args[0] {
        Yaml::Array(v) => {
            let at = index(args, 1)?;
            let mut out = v.clone();
            *out.get_mut(at).ok_or_else(|| Fault::at(1, "range", format!("index {at} is out of range for length {}", v.len())))? = args[2].clone();
            Ok(Yaml::Array(out))
        }
        Yaml::Hash(h) => {
            // Replacing an existing key keeps its position; `insert` alone would move it to the end.
            let mut out = h.clone();
            let k = key(args, 1)?;
            match out.get_mut(k) {
                Some(slot) => *slot = args[2].clone(),
                None => drop(out.insert(k.clone(), args[2].clone())),
            }
            Ok(Yaml::Hash(out))
        }
        other => Err(Fault::expected(0, "list or map", other)),
    }
}

fn append(args: &[Yaml]) -> Result<Yaml, Fault> {
    match &args[0] {
        Yaml::Array(v) => Ok(Yaml::Array(v.iter().chain(&args[1..]).cloned().collect())),
        other => Err(Fault::expected(0, "list", other)),
    }
}

fn keys(value: &Yaml) -> Result<Yaml, Fault> {
    match value {
        Yaml::Hash(h) => Ok(Yaml::Array(h.keys().cloned().collect())),
        other => Err(Fault::expected(0, "map", other)),
    }
}

/// Map: key present. List: element present (by `eq`). String: substring present.
fn has(args: &[Yaml]) -> Result<Yaml, Fault> {
    let found = match (&args[0], &args[1]) {
        (Yaml::Hash(h), key @ Yaml::String(_)) => h.contains_key(key),
        (Yaml::Hash(_), other) => return Err(Fault::expected(1, "string key", other)),
        (Yaml::Array(v), needle) => v.iter().any(|element| equal(element, needle)),
        (Yaml::String(s), Yaml::String(needle)) => s.contains(needle.as_str()),
        (Yaml::String(_), other) => return Err(Fault::expected(1, "string", other)),
        (other, _) => return Err(Fault::expected(0, "string, list or map", other)),
    };
    Ok(Yaml::Boolean(found))
}

fn values(value: &Yaml) -> Result<Yaml, Fault> {
    match value {
        Yaml::Hash(h) => Ok(Yaml::Array(h.values().cloned().collect())),
        other => Err(Fault::expected(0, "map", other)),
    }
}

/// Each element that occurs more than once (by `eq`), listed once, in order of first repetition.
fn duplicates(value: &Yaml) -> Result<Yaml, Fault> {
    let Yaml::Array(items) = value else { return Err(Fault::expected(0, "list", value)) };
    let mut repeated: Vec<Yaml> = Vec::new();
    for (i, item) in items.iter().enumerate() {
        if items[..i].iter().any(|earlier| equal(earlier, item)) && !repeated.iter().any(|seen| equal(seen, item)) {
            repeated.push(item.clone());
        }
    }
    Ok(Yaml::Array(repeated))
}

/// `[dict, key, value, key, value, ...]`: a map whose keys and values are evaluated expressions.
fn dict(args: &[Yaml]) -> Result<Yaml, Fault> {
    if args.len() % 2 != 0 {
        return Err(Fault::whole("arity", "`dict` takes alternating keys and values".into()));
    }
    let mut out = yaml_rust2::yaml::Hash::new();
    for (i, pair) in args.chunks_exact(2).enumerate() {
        let key = key(args, 2 * i)?;
        out.insert(key.clone(), pair[1].clone());
    }
    Ok(Yaml::Hash(out))
}
