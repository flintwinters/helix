//! Streaming filesystem observations. Search policy is ordinary embedded Helix.
use super::source::reflect;
use crate::{graph::Graph, model::Modules};
use serde_json::{Value as J, json};
use std::{
    collections::HashSet,
    io::{self, Write},
    path::{Path, PathBuf},
    process::ExitCode,
};
const EXCLUDES: &[&str] = &[
    ".git",
    ".hg",
    ".svn",
    ".worktrees",
    ".claude",
    ".codex",
    "target",
    "runtime",
    "node_modules",
    "vendor",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
];
const MAX_FILE: u64 = 1_000_000;
struct Index<W: Write> {
    out: W,
    seen: HashSet<PathBuf>,
    std: HashSet<String>,
    roots: Vec<PathBuf>,
    complete: bool,
    count: usize,
    exclude: bool,
}
impl<W: Write> Index<W> {
    fn emit(&mut self, record: J) -> io::Result<()> {
        serde_json::to_writer(&mut self.out, &record)?;
        self.out.write_all(b"\n")?;
        self.out.flush()
    }
    fn diagnostic(&mut self, source: &str, message: String) -> io::Result<()> {
        self.complete = false;
        self.emit(json!({"version":1,"kind":"diagnostic","source":source,"reference":{"source":source,"path":[]},"location":null,"message":message}))
    }
    fn parse_error(&mut self, source: &str, error: crate::model::HelixError) -> io::Result<()> {
        self.complete = false;
        let source = error.file.as_deref().unwrap_or(source);
        self.emit(json!({"version":1,"kind":"diagnostic","source":source,"reference":{"source":source,"path":[]},"location":{"line":error.span.line,"column":error.span.col},"message":error.message,"error":super::source::json(&error.to_value().plain_yaml().unwrap())}))
    }
    fn source(&mut self, source: &str, text: &str, file: Option<&Path>) -> io::Result<()> {
        let mut graph = match Graph::parse(text) {
            Ok(g) => g,
            Err(e) => return self.parse_error(source, e),
        };
        let modules = match crate::link::link(&mut graph, file, Some(&self.roots)) {
            Ok(m) => m,
            Err(e) => {
                self.parse_error(source, e)?;
                Modules::default()
            }
        };
        let mut record = reflect(&graph, source, &modules);
        record["text"] = json!(text);
        // Revision is exact source text, avoiding hash collisions and extra dependencies.
        record["revision"] = json!(text);
        self.emit(record.clone())?;
        self.count += 1;
        for node in record["nodes"].as_array().unwrap() {
            if node["kind"] == "invalid" {
                self.complete = false;
                self.emit(json!({"version":1,"kind":"diagnostic","source":source,"reference":node["reference"],"location":node["location"],"message":node["error"]}))?;
            }
            if node["kind"] == "function" {
                let mut candidate = node.clone();
                candidate["version"] = json!(1);
                candidate["kind"] = json!("function");
                candidate["source"] = json!(source);
                candidate["revision"] = json!(text);
                candidate["name"] = candidate["reference"]["path"]
                    .as_array()
                    .and_then(|p| p.last())
                    .cloned()
                    .unwrap_or(json!("<root>"));
                candidate["basis"] = json!("syntax");
                self.emit(candidate)?;
            }
            if node["kind"] == "call" || node["kind"] == "reference" {
                let mut edge = node.clone();
                edge["version"] = json!(1);
                edge["kind"] = json!(if node["kind"] == "call" {
                    "call"
                } else {
                    "reference"
                });
                edge["source"] = json!(source);
                edge["basis"] = json!("syntax");
                edge["from"] = node["enclosing_function"].clone();
                edge["to"] = node["target"].clone();
                edge["resolved"] =
                    json!(!node["target"].is_null() || node["binding"]["kind"] == "builtin");
                self.emit(edge)?;
            }
        }
        // Imported definitions keep their own source-qualified identities; they are indexed
        // separately when selected, while edges may point across this source boundary.
        Ok(())
    }
    fn visit(&mut self, path: &Path, explicit: bool) -> io::Result<()> {
        let metadata = match std::fs::symlink_metadata(path) {
            Ok(m) => m,
            Err(e) => return self.diagnostic(&path.display().to_string(), e.to_string()),
        };
        if metadata.file_type().is_symlink() && !explicit {
            return Ok(());
        }
        if path.is_dir() {
            if !explicit
                && self.exclude
                && path
                    .file_name()
                    .is_some_and(|n| EXCLUDES.iter().any(|x| n == *x))
            {
                return Ok(());
            }
            let canonical = match path.canonicalize() {
                Ok(p) => p,
                Err(e) => return self.diagnostic(&path.display().to_string(), e.to_string()),
            };
            if !self.seen.insert(canonical) {
                return Ok(());
            }
            let entries = match std::fs::read_dir(path) {
                Ok(e) => e,
                Err(e) => return self.diagnostic(&path.display().to_string(), e.to_string()),
            };
            for entry in entries {
                match entry {
                    Ok(e) => self.visit(&e.path(), false)?,
                    Err(e) => self.diagnostic(&path.display().to_string(), e.to_string())?,
                }
            }
            return Ok(());
        }
        let ext = path.extension().and_then(|s| s.to_str()).unwrap_or("");
        let is_source =
            matches!(ext, "yaml" | "yml") || explicit && !matches!(ext, "md" | "txt" | "markdown");
        if !explicit && !matches!(ext, "yaml" | "yml" | "md" | "txt" | "markdown") {
            return Ok(());
        }
        let canonical = match path.canonicalize() {
            Ok(p) => p,
            Err(e) => return self.diagnostic(&path.display().to_string(), e.to_string()),
        };
        if !self.seen.insert(canonical.clone()) {
            return Ok(());
        }
        let identity = canonical.display().to_string();
        if metadata.len() > MAX_FILE {
            return self.diagnostic(&identity, "file exceeds 1000000 bytes".into());
        }
        if !path.is_file() {
            return self.diagnostic(&identity, "source must be a regular file".into());
        }
        let text = match super::stream::read_bounded(&identity, MAX_FILE) {
            Ok(t) => t,
            Err(e) => return self.diagnostic(&identity, e.to_string()),
        };
        if is_source {
            self.source(&identity, &text, Some(&canonical))
        } else {
            self.count += 1;
            self.emit(json!({"version":1,"kind":"documentation","source":identity,"reference":{"source":identity,"path":[]},"location":{"line":1,"column":1},"text":text}))
        }
    }
    fn embedded(&mut self, name: &str) -> io::Result<()> {
        let name = name.trim_start_matches("std:");
        if !self.std.insert(name.into()) {
            return Ok(());
        }
        match crate::std_source(name) {
            Some(s) => self.source(&format!("std:{name}"), s, None),
            None => self.diagnostic(name, "unknown standard module".into()),
        }
    }
}
pub fn command(args: &[String]) -> ExitCode {
    if args.iter().any(|s| matches!(s.as_str(), "--help" | "-h")) {
        println!(
            "usage: helix search index [PATH ...] [--library PATH_OR_STD_MODULE] [--no-std] [--no-default-excludes]\n       helix search parse FILE_OR_STDIN\n       helix search probe --cases FILE [--harness FILE] [--root PATH] [--max-steps N]"
        );
        return ExitCode::SUCCESS;
    }
    match run(args) {
        Ok(complete) => {
            if complete {
                ExitCode::SUCCESS
            } else {
                ExitCode::FAILURE
            }
        }
        Err((code, message)) => {
            eprintln!("{message}");
            ExitCode::from(code)
        }
    }
}
fn run(args: &[String]) -> Result<bool, (u8, String)> {
    if args.first().is_some_and(|s| s == "parse") {
        if args.len() != 2 {
            return Err((2, "search parse requires FILE or -".into()));
        }
        let text = super::stream::read(&args[1]).map_err(|e| (2, e))?;
        let mut index = Index {
            out: io::stdout().lock(),
            seen: HashSet::new(),
            std: HashSet::new(),
            roots: vec![],
            complete: true,
            count: 0,
            exclude: true,
        };
        match Graph::parse(&text) {
            Ok(graph) => index.emit(reflect(&graph, &args[1], &Modules::default())),
            Err(e) => index.parse_error(&args[1], e),
        }
        .map_err(|e| (1, e.to_string()))?;
        return Ok(index.complete);
    }
    if !args.first().is_some_and(|s| s == "index") {
        return Err((2,"usage: helix search index [PATH ...] [--library PATH_OR_STD_MODULE] [--no-std] [--no-default-excludes] | parse FILE_OR_STDIN | probe --cases FILE [--harness FILE]".into()));
    }
    let (mut paths, mut libraries, mut standard, mut exclude) = (vec![], vec![], true, true);
    let mut iter = args[1..].iter();
    while let Some(arg) = iter.next() {
        match arg.as_str() {
            "--no-std" => standard = false,
            "--no-default-excludes" => exclude = false,
            "--library" => libraries.push(
                iter.next()
                    .ok_or((2, "--library requires a value".into()))?
                    .clone(),
            ),
            s if s.starts_with('-') => return Err((2, format!("unknown option {s}"))),
            s => paths.push(PathBuf::from(s)),
        }
    }
    if paths.is_empty() {
        paths.push(PathBuf::from("."));
    }
    let roots = paths
        .iter()
        .chain(
            libraries
                .iter()
                .filter(|s| !s.starts_with("std:"))
                .map(Path::new)
                .map(Path::to_path_buf)
                .collect::<Vec<_>>()
                .iter(),
        )
        .filter_map(|p| {
            if p.is_dir() {
                p.canonicalize().ok()
            } else {
                p.canonicalize()
                    .ok()
                    .and_then(|p| p.parent().map(Path::to_path_buf))
            }
        })
        .collect();
    let mut index = Index {
        out: io::stdout().lock(),
        seen: HashSet::new(),
        std: HashSet::new(),
        roots,
        complete: true,
        count: 0,
        exclude,
    };
    for path in paths {
        index.visit(&path, true).map_err(|e| (1, e.to_string()))?;
    }
    for library in libraries {
        if library.starts_with("std:") {
            index.embedded(&library)
        } else {
            index.visit(Path::new(&library), true)
        }
        .map_err(|e| (1, e.to_string()))?;
    }
    if standard {
        for name in crate::std_modules() {
            index.embedded(name).map_err(|e| (1, e.to_string()))?;
        }
    }
    index
        .emit(json!({"version":1,"kind":"summary","complete":index.complete,"sources":index.count}))
        .map_err(|e| (1, e.to_string()))?;
    Ok(index.complete)
}
