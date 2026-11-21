//! Hierarchical lookup of the embedded standard library: a module list, one module's export
//! index, then one export's source. Each level names the next, so a reader never needs the
//! whole library to find one function. Signatures and documentation come from the module
//! source itself (its parameter annotations and the comments written before each key).
use std::process::ExitCode;

use serde_json::{Value as J, json};

use crate::graph::{Graph, Syntax, ROOT, nearest};
use crate::model::NodeId;
use crate::library;
use super::find::Ty;

/// Values longer than this are cut in an index line; signatures are never cut.
const SHOWN: usize = 90;

pub(crate) struct Module {
    pub(crate) name: String,
    /// The comment block that opens the module.
    pub(crate) doc: Vec<&'static str>,
    pub(crate) exports: Vec<Export>,
}

pub(crate) struct Export {
    pub(crate) name: String,
    /// `name(param: type, ...) -> result` for a function, otherwise `name: <value source>`.
    pub(crate) signature: String,
    /// The comment lines written directly before the key.
    pub(crate) doc: Vec<&'static str>,
    /// The written parameter and result annotations when the export is a `fn`.
    pub(crate) shape: Option<Shape>,
    /// The key's complete source text, including its comment.
    source: Text,
}

/// A function's written annotations; `None` marks what the source leaves unwritten.
pub(crate) struct Shape {
    pub(crate) params: Vec<(String, Option<Ty>)>,
    pub(crate) result: Option<Ty>,
}

/// Source as written in the module, or canonically emitted when no written slice holds it.
enum Text {
    Written(&'static str),
    Owned(String),
}

impl std::ops::Deref for Text {
    type Target = str;
    fn deref(&self) -> &str {
        match self {
            Self::Written(text) => text,
            Self::Owned(text) => text,
        }
    }
}

/// `helix std [MODULE | MODULE.EXPORT] [--source | --json]`.
pub fn command(args: &[&str]) -> ExitCode {
    let (json, source) = (args.contains(&"--json"), args.contains(&"--source"));
    let rest: Vec<&str> = args.iter().copied().filter(|arg| !matches!(*arg, "--json" | "--source")).collect();
    match rest.as_slice() {
        [] if !source => list(json),
        [path] => {
            let path = path.trim_start_matches("std:");
            let (module, export) = path.split_once('.').map_or((path, None), |(m, e)| (m, Some(e)));
            let Some(text) = library::source(module) else {
                return unknown("standard module", module, library::names(), "list modules with `helix std`");
            };
            match (export, source) {
                (None, true) => {
                    print!("{text}");
                    ExitCode::SUCCESS
                }
                (None, false) => index(&parse(module, text), json),
                (Some(export), _) => {
                    let module = parse(module, text);
                    match module.exports.iter().find(|e| e.name == export) {
                        Some(found) => show(&module, found, json),
                        None => unknown(
                            &format!("export of std:{}", module.name),
                            export,
                            module.exports.iter().map(|e| e.name.as_str()),
                            &format!("list its exports with `helix std {}`", module.name),
                        ),
                    }
                }
            }
        }
        _ => {
            eprintln!("usage: helix std [MODULE | MODULE.EXPORT] [--json]; helix std MODULE --source");
            ExitCode::from(2)
        }
    }
}

fn list(json: bool) -> ExitCode {
    let modules = modules();
    if json {
        let rows: Vec<_> = modules.iter().map(|m| json!({"module": format!("std:{}", m.name), "summary": summary(&m.doc)})).collect();
        println!("{}", J::Array(rows));
        return ExitCode::SUCCESS;
    }
    let width = modules.iter().map(|m| m.name.len()).max().unwrap_or(0) + 4;
    for module in &modules {
        println!("{:width$}  {}", format!("std:{}", module.name), summary(&module.doc));
    }
    println!("\nnext: `helix std MODULE` lists a module's exports with signatures");
    ExitCode::SUCCESS
}

fn index(module: &Module, json: bool) -> ExitCode {
    if json {
        let exports: Vec<_> = module.exports.iter().map(|e| json!({"name": e.name, "signature": e.signature, "summary": summary(&e.doc)})).collect();
        println!("{}", json!({"module": format!("std:{}", module.name), "doc": module.doc, "exports": exports}));
        return ExitCode::SUCCESS;
    }
    println!("std:{}", module.name);
    for line in &module.doc {
        println!("  {line}");
    }
    println!();
    for export in &module.exports {
        match summary(&export.doc).as_str() {
            "" => println!("{}", export.signature),
            summary => println!("{}    # {summary}", export.signature),
        }
    }
    println!("\nnext: `helix std {0}.EXPORT` shows one export's source; `helix std {0} --source` the whole module", module.name);
    ExitCode::SUCCESS
}

fn show(module: &Module, export: &Export, json: bool) -> ExitCode {
    if json {
        println!("{}", json!({"module": format!("std:{}", module.name), "name": export.name, "signature": export.signature, "doc": export.doc, "source": &*export.source}));
        return ExitCode::SUCCESS;
    }
    println!("{}    # std:{}", export.signature, module.name);
    println!("import: `{0}: !call [import, 'std:{0}']`, then refer to `{0}.{1}`\n", module.name, export.name);
    println!("{}", &*export.source);
    ExitCode::SUCCESS
}

/// Reject `name`, naming the closest candidate, else the candidates containing it, and where to
/// list them all.
pub(crate) fn unknown<'a>(what: &str, name: &str, candidates: impl Iterator<Item = &'a str>, next: &str) -> ExitCode {
    let candidates: Vec<_> = candidates.collect();
    let containing: Vec<_> = candidates.iter().filter(|candidate| candidate.contains(name)).copied().collect();
    match nearest(name, candidates.iter().copied()) {
        Some(close) => eprintln!("unknown {what} `{name}`; did you mean `{close}`? {next}"),
        None if !containing.is_empty() => eprintln!("unknown {what} `{name}`; names containing it: {}; {next}", containing.join(", ")),
        None => eprintln!("unknown {what} `{name}`; {next}"),
    }
    ExitCode::from(2)
}

/// The first sentence of a comment block, cut to an index line's width.
fn summary(doc: &[&str]) -> String {
    let joined = doc.join(" ");
    let sentence = joined.find(". ").map_or(joined.as_str(), |end| &joined[..=end]).trim_end_matches('.');
    match sentence.char_indices().nth(SHOWN) {
        Some((cut, _)) => format!("{}…", &sentence[..cut]),
        None => sentence.to_string(),
    }
}

/// A module's exports with their documentation and source. Embedded modules are canonically
/// formatted, so a root map's keys each begin a column-zero line: a key's text starts at the
/// comment block directly above it and ends where the next key's text starts. Exports assembled
/// by a root `let` show the canonical source of their definitions instead.
pub(crate) fn parse(name: &str, text: &'static str) -> Module {
    let mut lines = Vec::new();
    let mut offset = 0;
    for line in text.split_inclusive('\n') {
        lines.push((offset, line.trim_end_matches('\n')));
        offset += line.len();
    }
    let comment = |line: &str| line.starts_with('#');
    let opening = lines.iter().take_while(|(_, line)| comment(line)).count();
    let doc = lines[..opening].iter().map(|(_, line)| uncomment(line)).collect();
    let graph = Graph::parse(text).expect("embedded standard module parses");
    let exported = library::exported(&graph);
    if !matches!(graph.syntax(ROOT), Syntax::Mapping(_)) {
        let exports = exported
            .into_iter()
            .map(|(key, node)| Export {
                signature: signature(&graph, &key, node),
                shape: shape(&graph, node),
                doc: Vec::new(),
                source: Text::Owned(crate::emit::entry(&graph, &key, node).trim_end_matches('\n').to_string()),
                name: key,
            })
            .collect();
        return Module { name: name.into(), doc, exports };
    }
    // (first line of the key's text, the key's own line)
    let mut keys = Vec::new();
    let mut pending = None;
    for (i, (_, line)) in lines.iter().enumerate().skip(opening) {
        if comment(line) {
            pending.get_or_insert(i);
        } else if line.is_empty() || line.starts_with([' ', '-']) {
            pending = None;
        } else {
            keys.push((pending.take().unwrap_or(i), i));
        }
    }
    assert_eq!(exported.len(), keys.len(), "std:{name} keeps every root key at column zero");
    let start = |line: usize| lines.get(line).map_or(text.len(), |(offset, _)| *offset);
    let exports = exported
        .into_iter()
        .zip(&keys)
        .enumerate()
        .map(|(k, ((key, node), &(first, own)))| Export {
            signature: signature(&graph, &key, node),
            shape: shape(&graph, node),
            doc: lines[first..own].iter().map(|(_, line)| uncomment(line)).collect(),
            source: Text::Written(text[start(first)..keys.get(k + 1).map_or(text.len(), |&(next, _)| start(next))].trim_end_matches('\n')),
            name: key,
        })
        .collect();
    Module { name: name.into(), doc, exports }
}

fn uncomment(line: &'static str) -> &'static str {
    line.trim_start_matches('#').trim()
}

/// Every embedded module, parsed.
pub(crate) fn modules() -> Vec<Module> {
    library::names().map(|name| parse(name, library::source(name).unwrap())).collect()
}

fn shape(graph: &Graph, node: NodeId) -> Option<Shape> {
    if graph.form(node) != Some("fn") {
        return None;
    }
    let params = graph.parameters(node).ok()?.into_iter().map(|(name, ty)| (name, ty.map(|ty| Ty::from_graph(graph, ty)))).collect();
    let result = graph.call(node).and_then(|items| items.get(3)).map(|&r| Ty::from_graph(graph, r));
    Some(Shape { params, result })
}

/// `name(param: type, ...) -> result` from a `fn`'s written annotations; any other value
/// shows its leading source.
fn signature(graph: &Graph, name: &str, node: NodeId) -> String {
    let annotation = |id: NodeId| match graph.syntax(id) {
        Syntax::Reference(name) => name.to_string(),
        _ => crate::emit::flow(graph, id),
    };
    if graph.form(node) == Some("fn") {
        if let Ok(params) = graph.parameters(node) {
            let params: Vec<_> = params
                .into_iter()
                .map(|(param, ty)| ty.map_or_else(|| param.clone(), |ty| format!("{param}: {}", annotation(ty))))
                .collect();
            let result = graph.call(node).and_then(|items| items.get(3)).map_or_else(String::new, |&r| format!(" -> {}", annotation(r)));
            return format!("{name}({}){result}", params.join(", "));
        }
    }
    let value = crate::emit::flow(graph, node);
    match value.char_indices().nth(SHOWN) {
        Some((cut, _)) => format!("{name}: {}…", &value[..cut]),
        None => format!("{name}: {value}"),
    }
}
