//! Correction hints for rejected names, derived only from authoritative sources: the builtin
//! registry, lexical scope and the standard library's exports. Hints are advisory; they never
//! change an error's kind, message or blame.

use super::{Graph, NodeId};
use crate::model::HelixError;
use crate::value::{Hash, Value};
use crate::{builtins, library};

impl Graph {
    /// An `unbound` error for `name` written at `id`, naming the likely correction.
    pub(crate) fn unbound(&self, id: NodeId, name: &str, actor: bool) -> HelixError {
        let role = if actor { "actor" } else { "symbol" };
        self.error(id, "unbound", format!("unbound {role} `{name}`")).with_hint(self.unbound_hint(id, name, actor))
    }

    fn unbound_hint(&self, id: NodeId, name: &str, actor: bool) -> Option<String> {
        let (head, member) = match name.split_once('.') {
            Some((head, member)) => (head, Some(member)),
            None => (name, None),
        };
        let mut hints = Vec::new();
        if let (false, None, Some(arity)) = (actor, member, builtins::arity(name)) {
            hints.push(format!(
                "`{name}` is a builtin actor, not a value: call it as `!call [{name}, ...]`, or wrap it to pass it as a function: `!call [fn, [{params}], !call [{name}, {args}]]`",
                params = parameters(arity).join(", "),
                args = parameters(arity).iter().map(|p| format!("!ref {p}")).collect::<Vec<_>>().join(", "),
            ));
        }
        if member.is_some() && library::source(head).is_some() && self.lexical_site(id, head).is_none() {
            hints.push(format!("bind the module first: `{head}: !call [import, 'std:{head}']`"));
        } else if member.is_none() {
            let modules: Vec<_> = library::exports()
                .iter()
                .filter(|(_, exports)| exports.iter().any(|export| export == name))
                .map(|(module, _)| *module)
                .collect();
            if let Some(&module) = modules.first() {
                let usage = if actor { format!("!call [{module}.{name}, ...]") } else { format!("!ref {module}.{name}") };
                let also = match &modules[1..] {
                    [] => String::new(),
                    more => format!(" (also exported by {})", more.iter().map(|m| format!("std:{m}")).collect::<Vec<_>>().join(", ")),
                };
                hints.push(format!(
                    "`{name}` is exported by std:{module}{also}: bind `{module}: !call [import, 'std:{module}']` and write `{usage}`"
                ));
            }
        }
        if !hints.is_empty() {
            return Some(hints.join("; "));
        }
        let visible = self.visible_names(id);
        // Builtins are callable but not values; primitive types are values but not callable.
        let fixed: Vec<&str> = match (actor, member) {
            (true, None) => builtins::names().collect(),
            (false, None) => builtins::primitives().to_vec(),
            (_, Some(_)) => Vec::new(),
        };
        let candidates = visible.iter().map(String::as_str).chain(fixed);
        if let Some(nearest) = nearest(head, candidates) {
            let spelled = member.map_or_else(|| nearest.to_string(), |member| format!("{nearest}.{member}"));
            return Some(format!("did you mean `{spelled}`?"));
        }
        None
    }
}

/// The member names of a map value, or the type name of anything else.
pub(crate) fn members_of(value: &Value) -> Result<Vec<String>, &'static str> {
    match value {
        Value::Hash(map) => Ok(keys(map)),
        other => Err(builtins::type_name(other)),
    }
}

/// Why selecting `member` below `written`, whose members are `found`, failed.
pub(crate) fn missing_member(written: &str, found: Result<Vec<String>, &str>, member: &str) -> String {
    let keys = match found {
        Ok(keys) => keys,
        Err(type_name) => return format!("`{written}` is {}, not a map, so it has no member `{member}`", article(type_name)),
    };
    match nearest(member, keys.iter().map(String::as_str)) {
        Some(nearest) => format!("`{written}` has no member `{member}`; did you mean `{written}.{nearest}`?"),
        None => format!("`{written}` has no member `{member}`; {}", members(written, &keys)),
    }
}

/// Why a call head written as `written` that evaluated to the non-function `value` cannot be called.
pub(crate) fn not_callable(written: Option<&str>, value: &Value) -> Option<String> {
    let (Some(written), Value::Hash(map)) = (written, value) else {
        return None;
    };
    let keys = keys(map);
    let example = keys.first().map_or_else(String::new, |key| format!(" as `!call [{written}.{key}, ...]`"));
    Some(format!("`{written}` is a map; call one of its function members{example}; {}", members(written, &keys)))
}

fn keys(map: &Hash) -> Vec<String> {
    map.keys().filter_map(|key| match key {
        Value::String(key) => Some(key.to_string()),
        _ => None,
    }).collect()
}

/// A bounded listing of a map's members.
fn members(written: &str, keys: &[String]) -> String {
    const SHOWN: usize = 12;
    match keys.len() {
        0 => format!("`{written}` is empty"),
        n if n <= SHOWN => format!("members: {}", keys.join(", ")),
        n => format!("members: {}, … ({} more)", keys[..SHOWN].join(", "), n - SHOWN),
    }
}

fn article(noun: &str) -> String {
    let an = noun.starts_with(['a', 'e', 'i', 'o', 'u']);
    format!("{} {noun}", if an { "an" } else { "a" })
}

/// Placeholder parameter names for a builtin's minimum arity, at least one.
fn parameters((min, _): (usize, Option<usize>)) -> Vec<String> {
    (0..min.max(1)).map(|i| char::from(b'a' + (i as u8 % 26)).to_string()).collect()
}

/// The closest candidate within a third of the name's length, first one on ties.
fn nearest<'a>(name: &str, candidates: impl IntoIterator<Item = &'a str>) -> Option<&'a str> {
    let limit = (name.chars().count() / 3).max(1);
    candidates
        .into_iter()
        .filter(|candidate| *candidate != name)
        .map(|candidate| (distance(name, candidate), candidate))
        .filter(|(d, _)| *d <= limit)
        .min_by_key(|(d, _)| *d)
        .map(|(_, candidate)| candidate)
}

/// Edit distance over characters where an adjacent transposition counts as one edit
/// (optimal string alignment), so `prot` is one edit from `port`.
fn distance(a: &str, b: &str) -> usize {
    let (a, b): (Vec<char>, Vec<char>) = (a.chars().collect(), b.chars().collect());
    let mut d = vec![vec![0; b.len() + 1]; a.len() + 1];
    for (i, row) in d.iter_mut().enumerate() {
        row[0] = i;
    }
    for j in 0..=b.len() {
        d[0][j] = j;
    }
    for i in 1..=a.len() {
        for j in 1..=b.len() {
            let cost = usize::from(a[i - 1] != b[j - 1]);
            d[i][j] = (d[i - 1][j] + 1).min(d[i][j - 1] + 1).min(d[i - 1][j - 1] + cost);
            if i > 1 && j > 1 && a[i - 1] == b[j - 2] && a[i - 2] == b[j - 1] {
                d[i][j] = d[i][j].min(d[i - 2][j - 2] + 1);
            }
        }
    }
    d[a.len()][b.len()]
}
