//! `helix find`: ranked lookup over builtins and the standard library, with structural filters on
//! written parameter and result types. Types are compared as written (`list_of(string)`), never
//! evaluated; what the source leaves unannotated stays explicitly unseen by type filters.
use std::{fmt, process::ExitCode};

use serde_json::{Value as J, json};

use super::catalog::{self, Shape};
use crate::{
    builtins,
    graph::{Graph, Syntax},
    model::NodeId,
};

/// A type annotation as written: a name, an application such as `list_of(string)`, other source
/// text, or the query wildcard `_`.
#[derive(Clone, Debug, PartialEq)]
pub(crate) enum Ty {
    Any,
    Name(String),
    App(String, Vec<Ty>),
    Source(String),
}

impl Ty {
    pub(crate) fn from_graph(graph: &Graph, id: NodeId) -> Ty {
        match graph.syntax(id) {
            Syntax::Reference(name) => Ty::Name(name.to_string()),
            Syntax::Call(items) => match graph.syntax(items[0]) {
                Syntax::Scalar(yaml_rust2::Yaml::String(head)) => Ty::App(head.clone(), items[1..].iter().map(|&item| Ty::from_graph(graph, item)).collect()),
                _ => Ty::Source(crate::emit::flow(graph, id)),
            },
            _ => Ty::Source(crate::emit::flow(graph, id)),
        }
    }

    /// `list_of(string)`, `string` or `_`; arguments are separated by commas.
    fn parse(text: &str) -> Result<Ty, String> {
        let chars: Vec<char> = text.chars().collect();
        let mut at = 0;
        let ty = Self::parse_at(&chars, &mut at)?;
        match chars.get(at..).filter(|rest| !rest.is_empty()) {
            None => Ok(ty),
            Some(rest) => Err(format!("unexpected `{}` after the type", rest.iter().collect::<String>())),
        }
    }

    fn parse_at(chars: &[char], at: &mut usize) -> Result<Ty, String> {
        let skip = |at: &mut usize| while chars.get(*at).is_some_and(|c| c.is_whitespace()) { *at += 1 };
        skip(at);
        let start = *at;
        while chars.get(*at).is_some_and(|c| c.is_alphanumeric() || matches!(c, '_' | '.' | ':')) {
            *at += 1;
        }
        let name: String = chars[start..*at].iter().collect();
        if name.is_empty() {
            return Err("expected a type name".into());
        }
        skip(at);
        if chars.get(*at) != Some(&'(') {
            return Ok(if name == "_" { Ty::Any } else { Ty::Name(name) });
        }
        *at += 1;
        let mut args = Vec::new();
        loop {
            args.push(Self::parse_at(chars, at)?);
            skip(at);
            match chars.get(*at) {
                Some(',') => *at += 1,
                Some(')') => {
                    *at += 1;
                    return Ok(Ty::App(name, args));
                }
                _ => return Err(format!("`{name}(` is not closed; separate arguments with `,` and end with `)`")),
            }
        }
    }

    /// The alternatives of a union, with `number` read as `union(integer, float)`; any other type
    /// is its own single alternative.
    fn alternatives(&self) -> Vec<Ty> {
        match self {
            Ty::Name(name) if name.rsplit('.').next() == Some("number") => vec![Ty::Name("integer".into()), Ty::Name("float".into())],
            Ty::App(head, args) if head == "union" => args.iter().flat_map(Ty::alternatives).collect(),
            other => vec![other.clone()],
        }
    }

    /// Whether this query type describes `actual`: `_` matches anything, and every alternative of
    /// the query must be one the actual type admits. Names match exactly or by their last dotted
    /// segment (`integer` finds `math.integer`), applications argument-wise. `any` is a name like
    /// any other: it matches only itself and `_`.
    fn matches(&self, actual: &Ty) -> bool {
        let admitted = actual.alternatives();
        self.alternatives().iter().all(|query| admitted.iter().any(|actual| query.matches_alternative(actual)))
    }

    fn matches_alternative(&self, actual: &Ty) -> bool {
        match (self, actual) {
            (Ty::Any, _) => true,
            (Ty::Name(query), Ty::Name(name)) => query == name || name.rsplit('.').next() == Some(query),
            (Ty::App(query, qargs), Ty::App(head, args)) => query == head && qargs.len() == args.len() && qargs.iter().zip(args).all(|(q, a)| q.matches(a)),
            (Ty::Source(query), Ty::Source(source)) => query == source,
            _ => false,
        }
    }
}

impl fmt::Display for Ty {
    fn fmt(&self, f: &mut fmt::Formatter) -> fmt::Result {
        match self {
            Ty::Any => f.write_str("_"),
            Ty::Name(name) | Ty::Source(name) => f.write_str(name),
            Ty::App(head, args) => {
                write!(f, "{head}(")?;
                for (i, arg) in args.iter().enumerate() {
                    write!(f, "{}{arg}", if i == 0 { "" } else { ", " })?;
                }
                f.write_str(")")
            }
        }
    }
}

impl Shape {
    /// `name(param: type, ...) -> result` with types in their short written form.
    fn render(&self, name: &str) -> String {
        let params: Vec<_> = self.params.iter().map(|(param, ty)| ty.as_ref().map_or_else(|| param.clone(), |ty| format!("{param}: {ty}"))).collect();
        let result = self.result.as_ref().map_or_else(String::new, |ty| format!(" -> {ty}"));
        format!("{name}({}){result}", params.join(", "))
    }
}

struct Entry {
    kind: &'static str,
    module: Option<String>,
    name: String,
    summary: String,
    /// Words searched beyond the name: documentation, family, parameter names.
    words: String,
    arity: (usize, Option<usize>),
    /// Written parameter and result types: every builtin, and each `fn` export of a std module.
    shape: Option<Shape>,
    signature: String,
    next: String,
}

#[derive(Default)]
struct Query {
    terms: Vec<String>,
    takes: Vec<Ty>,
    returns: Option<Ty>,
    arity: Option<usize>,
    scope: Option<String>,
    limit: usize,
    json: bool,
}

const USAGE: &str = "usage: helix find [WORDS...] [--takes TYPE]... [--returns TYPE] [--arity N] [--in builtins|std|MODULE] [--limit N] [--yaml]
types are written as in source annotations: string, integer, number, list_of(string), union(string, list); `_` matches any type, `type` is any type descriptor";

fn entries() -> Vec<Entry> {
    let mut found = Vec::new();
    for family in builtins::FAMILIES {
        for d in builtins::members(family) {
            let (types, result) = d.parameter_types();
            let params = d.parameter_names().into_iter().zip(types).map(|(name, ty)| match ty.strip_suffix("...") {
                Some(ty) => (format!("{name}..."), Some(Ty::parse(ty).expect("builtin types are valid type syntax"))),
                None => (name.to_string(), Some(Ty::parse(ty).expect("builtin types are valid type syntax"))),
            }).collect();
            let shape = Shape { params, result: Some(Ty::parse(result).expect("builtin types are valid type syntax")) };
            let shape_signature = shape.render(d.name);
            found.push(Entry {
                kind: "builtin",
                module: None,
                name: d.name.to_string(),
                summary: d.summary.to_string(),
                words: format!("{} {}", d.family(), d.signature),
                arity: d.arity,
                shape: Some(shape),
                signature: shape_signature,
                next: format!("helix builtins {}", d.name),
            });
        }
    }
    for module in catalog::modules() {
        for export in module.exports {
            let params = export.shape.as_ref().map_or(0, |shape| shape.params.len());
            found.push(Entry {
                kind: "std",
                words: format!("{} {}", export.doc.join(" "), module.doc.first().copied().unwrap_or("")),
                summary: export.doc.first().map(|line| line.to_string()).unwrap_or_default(),
                arity: (params, Some(params)),
                next: format!("helix std {}.{}", module.name, export.name),
                module: Some(module.name.clone()),
                signature: export.shape.as_ref().map_or(export.signature, |shape| shape.render(&export.name)),
                name: export.name,
                shape: export.shape,
            });
        }
    }
    found
}

/// Higher is a closer match; `None` when some term matches nothing in the entry.
fn rank(entry: &Entry, terms: &[String]) -> Option<u32> {
    let name = entry.name.to_lowercase();
    let module = entry.module.as_deref().unwrap_or("").to_lowercase();
    let summary = entry.summary.to_lowercase();
    let words = entry.words.to_lowercase();
    let params = entry.shape.iter().flat_map(|shape| shape.params.iter().map(|(param, _)| param.to_lowercase())).collect::<Vec<_>>();
    let mut score = 0;
    for term in terms {
        score += if name == *term {
            100
        } else if name.starts_with(term.as_str()) {
            60
        } else if name.contains(term.as_str()) {
            40
        } else if module == *term || module.contains(term.as_str()) {
            20
        } else if summary.contains(term.as_str()) {
            12
        } else if params.iter().any(|param| param.contains(term.as_str())) || words.contains(term.as_str()) {
            6
        } else {
            return None;
        };
    }
    Some(score)
}

/// Each wanted parameter type is matched by a distinct parameter.
fn takes(params: &[(String, Option<Ty>)], wanted: &[Ty]) -> bool {
    let Some((first, rest)) = wanted.split_first() else { return true };
    (0..params.len()).any(|i| {
        params[i].1.as_ref().is_some_and(|ty| first.matches(ty)) && {
            let mut others = params.to_vec();
            others.remove(i);
            takes(&others, rest)
        }
    })
}

fn parse(args: &[&str]) -> Result<Query, String> {
    let mut query = Query { limit: 20, ..Query::default() };
    let mut args = args.iter();
    let value = |flag: &str, args: &mut std::slice::Iter<&str>| args.next().map(|v| v.to_string()).ok_or_else(|| format!("{flag} requires a value\n{USAGE}"));
    let ty = |flag: &str, text: String| Ty::parse(&text).map_err(|why| format!("{flag} `{text}`: {why}; write a type as in annotations, such as `string` or `list_of(string)`"));
    while let Some(arg) = args.next() {
        match *arg {
            "--yaml" => query.json = true,
            "--takes" => query.takes.push(ty("--takes", value("--takes", &mut args)?)?),
            "--returns" => query.returns = Some(ty("--returns", value("--returns", &mut args)?)?),
            "--arity" => query.arity = Some(value("--arity", &mut args)?.parse().map_err(|_| "--arity requires a nonnegative integer".to_string())?),
            "--limit" => query.limit = value("--limit", &mut args)?.parse().map_err(|_| "--limit requires a nonnegative integer".to_string())?,
            "--in" => query.scope = Some(value("--in", &mut args)?),
            flag if flag.starts_with("--") => return Err(format!("unknown option `{flag}`\n{USAGE}")),
            word => query.terms.extend(word.split(|c: char| !(c.is_alphanumeric() || c == '_')).filter(|t| !t.is_empty()).map(str::to_lowercase)),
        }
    }
    Ok(query)
}

fn in_scope(entry: &Entry, scope: &str) -> bool {
    let module = scope.trim_start_matches("std:");
    match scope {
        "builtins" | "builtin" => entry.kind == "builtin",
        "std" => entry.kind == "std",
        _ => entry.module.as_deref() == Some(module),
    }
}

/// `helix find [WORDS...] [--takes TYPE]... [--returns TYPE] [--arity N] [--in SCOPE] [--limit N] [--yaml]`.
pub fn command(args: &[&str]) -> ExitCode {
    let query = match parse(args) {
        Ok(query) => query,
        Err(message) => {
            eprintln!("{message}");
            return ExitCode::from(2);
        }
    };
    let all = entries();
    if let Some(scope) = &query.scope {
        if !all.iter().any(|entry| in_scope(entry, scope)) {
            let modules: Vec<String> = all.iter().filter_map(|entry| entry.module.clone()).collect::<std::collections::BTreeSet<_>>().into_iter().collect();
            return catalog::unknown("scope", scope, modules.iter().map(String::as_str).chain(["builtins", "std"]), "use builtins, std, or a module name from `helix std`");
        }
    }
    let structural = !query.takes.is_empty() || query.returns.is_some();
    let mut hits = Vec::new();
    let mut unseen = 0;
    for entry in &all {
        let Some(score) = rank(entry, &query.terms) else { continue };
        if query.scope.as_deref().is_some_and(|scope| !in_scope(entry, scope)) {
            continue;
        }
        if query.arity.is_some_and(|n| n < entry.arity.0 || entry.arity.1.is_some_and(|max| n > max)) {
            continue;
        }
        if structural {
            match &entry.shape {
                Some(shape) if takes(&shape.params, &query.takes) && query.returns.as_ref().is_none_or(|wanted| shape.result.as_ref().is_some_and(|ty| wanted.matches(ty))) => {}
                Some(shape) => {
                    unseen += usize::from(shape.result.is_none() || shape.params.iter().any(|(_, ty)| ty.is_none()));
                    continue;
                }
                None => continue,
            }
        }
        hits.push((score, entry));
    }
    hits.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| (a.1.module.as_deref(), &a.1.name).cmp(&(b.1.module.as_deref(), &b.1.name))));
    let total = hits.len();
    hits.truncate(query.limit);
    let row = |score: u32, entry: &Entry| {
        let typed = |ty: &Option<Ty>| ty.as_ref().map_or(J::Null, |ty| json!(ty.to_string()));
        json!({
            "kind": entry.kind,
            "module": entry.module.as_ref().map(|m| format!("std:{m}")),
            "name": entry.name,
            "signature": entry.signature,
            "summary": entry.summary,
            "arity": {"min": entry.arity.0, "max": entry.arity.1},
            "params": entry.shape.as_ref().map(|shape| shape.params.iter().map(|(name, ty)| json!({"name": name, "type": typed(ty)})).collect::<Vec<_>>()),
            "returns": entry.shape.as_ref().map_or(J::Null, |shape| typed(&shape.result)),
            "score": score,
            "next": entry.next,
        })
    };
    if query.json {
        let rows: Vec<_> = hits.iter().map(|(score, entry)| row(*score, entry)).collect();
        super::source::print_report(&json!({"query": args, "total": total, "shown": rows.len(), "unseen_by_types": unseen, "results": rows}));
        return ExitCode::SUCCESS;
    }
    for (_, entry) in &hits {
        let place = entry.module.as_ref().map_or_else(|| "builtin ".to_string(), |m| format!("std:{m}."));
        let summary = if entry.summary.is_empty() { String::new() } else { format!("    # {}", entry.summary) };
        println!("{place}{}{summary}", entry.signature);
    }
    if hits.is_empty() {
        println!("no matches");
        println!("next: drop a word or filter; `helix std` lists modules, `helix builtins` lists builtins");
    } else {
        if total > hits.len() {
            println!("\n{} more; narrow with words, `--in MODULE`, or raise `--limit`", total - hits.len());
        }
        println!("\nnext: `{}` shows the first result; --yaml gives every field", hits[0].1.next);
    }
    if unseen > 0 {
        println!("note: {unseen} candidates lack type annotations, so --takes/--returns cannot see them");
    }
    ExitCode::SUCCESS
}
