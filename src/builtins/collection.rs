//! Strings, lists and maps. All operations are pure: they return a new value.
//! Strings index by character; maps have string keys and keep insertion order.

use yaml_rust2::Yaml;

use super::fault::Fault;
use super::value::equal;

pub(super) fn apply(actor: &str, args: &[Yaml]) -> Result<Yaml, Fault> {
    match actor {
        "len" => len(&args[0]),
        "concat" => concat(args),
        "slice" => slice(args),
        "get" => get(args),
        "set" => set(args),
        "append" => append(args),
        "keys" => keys(&args[0]),
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

fn key(args: &[Yaml], at: usize) -> Result<Yaml, Fault> {
    match &args[at] {
        key @ Yaml::String(_) => Ok(key.clone()),
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
            let chars: Vec<char> = s.chars().collect();
            let (start, end) = bounds(chars.len())?;
            Ok(Yaml::String(chars[start..end].iter().collect()))
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
        Yaml::Hash(h) => h.get(&key(args, 1)?),
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
            match out.get_mut(&k) {
                Some(slot) => *slot = args[2].clone(),
                None => drop(out.insert(k, args[2].clone())),
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
