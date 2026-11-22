//! Hierarchical reading of the reference documents and the builtin registry. A document is a
//! heading tree: the document list, one document's sections, then one section's own text with
//! its subsections named as the next step, so a reader never loads a whole reference to find
//! one rule. Builtins pair the registry's arity and family with the sentences that document them.
use std::process::ExitCode;

use serde_json::{Value as J, json};

use super::catalog::unknown;
use crate::{builtins, library};

/// `(name, file, text)` of the references embedded in the executable.
const DOCUMENTS: &[(&str, &str, &str)] = &[
    ("language", "LANGUAGE.md", include_str!("../../LANGUAGE.md")),
    ("library", "LIBRARY.md", include_str!("../../LIBRARY.md")),
    ("host", "HOST.md", include_str!("../../HOST.md")),
];

/// Documentation sentences shown for one builtin, and the width each is cut to.
const MENTIONS: usize = 5;
const SHOWN: usize = 320;

struct Document {
    name: String,
    file: String,
    lines: Vec<String>,
    /// The level-one heading and the text before the first section.
    title: String,
    intro: (usize, usize),
    sections: Vec<Section>,
}

struct Section {
    level: usize,
    title: String,
    slug: String,
    /// Line of the heading; end of its own text (the next heading); end including subsections.
    start: usize,
    own: usize,
    end: usize,
    parent: Option<usize>,
}

impl Document {
    fn parse(name: &str, file: &str, text: &str) -> Self {
        let lines: Vec<String> = text.lines().map(str::to_string).collect();
        let mut headings = Vec::new();
        let mut fenced = false;
        for (i, line) in lines.iter().enumerate() {
            if line.starts_with("```") {
                fenced = !fenced;
            } else if !fenced {
                let level = line.chars().take_while(|c| *c == '#').count();
                if level > 0 && line[level..].starts_with(' ') {
                    headings.push((i, level, line[level..].trim().to_string()));
                }
            }
        }
        let (title, body) = match headings.first() {
            Some((i, 1, title)) => (title.clone(), i + 1),
            _ => (file.to_string(), 0),
        };
        let headings: Vec<_> = headings.into_iter().filter(|(_, level, _)| *level > 1).collect();
        let mut sections: Vec<Section> = Vec::new();
        let mut used = Vec::new();
        for (k, (start, level, title)) in headings.iter().enumerate() {
            let next = |deeper: bool| {
                headings[k + 1..]
                    .iter()
                    .find(|(_, other, _)| deeper || other <= level)
                    .map_or(lines.len(), |(line, _, _)| *line)
            };
            let parent = sections.iter().rposition(|section| section.level < *level);
            let base = slug(title);
            let count = used.iter().filter(|used| **used == base).count();
            used.push(base.clone());
            sections.push(Section {
                level: *level,
                title: title.clone(),
                slug: if count == 0 { base } else { format!("{base}-{count}") },
                start: *start,
                own: next(true),
                end: next(false),
                parent,
            });
        }
        let intro = (body, sections.first().map_or(lines.len(), |section| section.start));
        Self { name: name.into(), file: file.into(), lines, title, intro, sections }
    }

    fn text(&self, (from, to): (usize, usize)) -> String {
        self.lines[from..to].join("\n").trim().to_string()
    }

    fn children(&self, parent: Option<usize>) -> impl Iterator<Item = &Section> {
        self.sections.iter().enumerate().filter(move |(_, s)| s.parent == parent).map(|(_, s)| s)
    }

    /// The first sentence of the introduction.
    fn summary(&self) -> String {
        let intro = self.text(self.intro);
        let paragraph = intro.split("\n\n").next().unwrap_or("").replace('\n', " ");
        first_sentence(&paragraph)
    }
}

/// GitHub's anchor rule: lowercase, keep letters, digits, `-` and `_`, spaces become `-`.
fn slug(title: &str) -> String {
    title
        .to_lowercase()
        .chars()
        .filter_map(|c| match c {
            ' ' => Some('-'),
            c if c.is_alphanumeric() || c == '-' || c == '_' => Some(c),
            _ => None,
        })
        .collect()
}

fn first_sentence(text: &str) -> String {
    let sentence = text.find(". ").map_or(text, |end| &text[..=end]);
    clip(sentence.trim())
}

fn clip(text: &str) -> String {
    match text.char_indices().nth(SHOWN) {
        Some((cut, _)) => format!("{}…", &text[..cut]),
        None => text.to_string(),
    }
}

fn embedded() -> impl Iterator<Item = Document> {
    DOCUMENTS.iter().map(|(name, file, text)| Document::parse(name, file, text))
}

/// An embedded reference by name or file name, or a Markdown file on disk.
fn open(name: &str) -> Result<Document, ExitCode> {
    let lowered = name.to_lowercase();
    if let Some(&(name, file, text)) = DOCUMENTS.iter().find(|(doc, file, _)| *doc == lowered || file.to_lowercase() == lowered) {
        return Ok(Document::parse(name, file, text));
    }
    if name.ends_with(".md") {
        return match std::fs::read_to_string(name) {
            Ok(text) => Ok(Document::parse(name, name, &text)),
            Err(error) => {
                eprintln!("cannot read {name}: {error}");
                Err(ExitCode::from(2))
            }
        };
    }
    // A section name asked for as a document: point at the document that holds it.
    let wanted = slug(name);
    let holders: Vec<String> = embedded()
        .filter(|document| document.sections.iter().any(|s| s.slug == wanted))
        .map(|document| format!("`helix docs {} {wanted}`", document.name))
        .collect();
    if !holders.is_empty() {
        eprintln!("`{name}` is a section, not a document; read it with {}", holders.join(" or "));
        return Err(ExitCode::from(2));
    }
    Err(unknown("document", name, DOCUMENTS.iter().map(|(doc, _, _)| *doc), "list documents with `helix docs`"))
}

/// `helix docs [DOC [SECTION]] [--yaml]`.
pub fn command(args: &[&str]) -> ExitCode {
    let json = args.contains(&"--yaml");
    let rest: Vec<&str> = args.iter().copied().filter(|arg| *arg != "--yaml").collect();
    let document = match rest.as_slice() {
        [] => return list(json),
        [name] | [name, _] => match open(name) {
            Ok(document) => document,
            Err(code) => return code,
        },
        _ => {
            eprintln!("{}", super::help::find("docs").unwrap().usage());
            return ExitCode::from(2);
        }
    };
    match rest.get(1) {
        None => tree(&document, json),
        Some(wanted) => match locate(&document, wanted) {
            Ok(index) => section(&document, index, json),
            Err(code) => code,
        },
    }
}

fn list(json: bool) -> ExitCode {
    let documents: Vec<_> = embedded().collect();
    if json {
        let rows: Vec<_> = documents
            .iter()
            .map(|d| json!({"document": d.name, "file": d.file, "title": d.title, "summary": d.summary(), "lines": d.lines.len(), "sections": d.sections.len()}))
            .collect();
        super::source::print_report(&J::Array(rows));
        return ExitCode::SUCCESS;
    }
    for document in &documents {
        println!("{:10} {} ({} lines, {} sections): {}", document.name, document.file, document.lines.len(), document.sections.len(), document.summary());
    }
    println!("\nnext: `helix docs DOC` lists a document's sections; `helix docs PATH.md` reads any Markdown file the same way");
    ExitCode::SUCCESS
}

fn tree(document: &Document, json: bool) -> ExitCode {
    if json {
        let sections: Vec<_> = document.sections.iter().map(|s| outline(document, s)).collect();
        super::source::print_report(&json!({"document": document.name, "file": document.file, "title": document.title, "intro": document.text(document.intro), "sections": sections}));
        return ExitCode::SUCCESS;
    }
    println!("{}: {} ({} lines)\n\n{}\n", document.file, document.title, document.lines.len(), document.text(document.intro));
    for section in &document.sections {
        println!("{}{} ({} lines)", "  ".repeat(section.level - 2), section.slug, section.end - section.start);
    }
    println!("\nnext: `helix docs {} SECTION` prints one section and names its subsections", document.name);
    ExitCode::SUCCESS
}

fn outline(document: &Document, section: &Section) -> J {
    json!({
        "section": section.slug,
        "title": section.title,
        "level": section.level,
        "line": section.start + 1,
        "lines": section.end - section.start,
        "parent": section.parent.map(|p| &document.sections[p].slug),
    })
}

/// A section by slug or heading text.
fn locate(document: &Document, wanted: &str) -> Result<usize, ExitCode> {
    let wanted = slug(wanted.trim_start_matches('#').trim());
    if let Some(index) = document.sections.iter().position(|s| s.slug == wanted) {
        return Ok(index);
    }
    let next = format!("list its sections with `helix docs {}`", document.name);
    Err(unknown(&format!("{} section", document.file), &wanted, document.sections.iter().map(|s| s.slug.as_str()), &next))
}

fn section(document: &Document, index: usize, json: bool) -> ExitCode {
    let section = &document.sections[index];
    let text = document.text((section.start, section.own));
    let children: Vec<_> = document.children(Some(index)).collect();
    if json {
        let mut value = outline(document, section);
        value["document"] = json!(document.name);
        value["text"] = json!(text);
        value["subsections"] = J::Array(children.iter().map(|child| outline(document, child)).collect());
        super::source::print_report(&value);
        return ExitCode::SUCCESS;
    }
    println!("{text}");
    if !children.is_empty() {
        println!("\nsubsections:");
        for child in &children {
            println!("  {} ({} lines)", child.slug, child.end - child.start);
        }
        println!("\nnext: `helix docs {} SUBSECTION`", document.name);
    }
    ExitCode::SUCCESS
}

/// `helix builtins [NAME] [--yaml]`.
pub fn builtins(args: &[&str]) -> ExitCode {
    let json = args.contains(&"--yaml");
    let rest: Vec<&str> = args.iter().copied().filter(|arg| *arg != "--yaml").collect();
    match rest.as_slice() {
        [] => families(json),
        [name] => match builtins::descriptor(name) {
            Some(found) => builtin(found, json),
            None => unknown_builtin(name),
        },
        _ => {
            eprintln!("{}", super::help::find("builtins").unwrap().usage());
            ExitCode::from(2)
        }
    }
}

fn arity((min, max): (usize, Option<usize>)) -> String {
    match max {
        Some(max) if max == min => min.to_string(),
        Some(max) => format!("{min}-{max}"),
        None => format!("{min}+"),
    }
}

fn families(json: bool) -> ExitCode {
    if json {
        let rows: Vec<_> = builtins::FAMILIES
            .iter()
            .flat_map(|family| builtins::members(family))
            .map(|d| json!({"name": d.name, "family": d.family(), "signature": d.signature, "types": d.types, "summary": d.summary, "min": d.arity.0, "max": d.arity.1}))
            .collect();
        super::source::print_report(&J::Array(rows));
        return ExitCode::SUCCESS;
    }
    println!("builtin actors by family, as `!call [NAME, parameters]`; `[, x]` is optional and `x...` repeats");
    for family in builtins::FAMILIES {
        println!("\n{family}:");
        for d in builtins::members(family) {
            println!("  [{}, {}] — {}", d.name, d.signature, d.summary);
        }
    }
    println!("\nnext: `helix builtins NAME` shows one builtin's documentation; library functions are under `helix std`");
    ExitCode::SUCCESS
}

/// The call a builtin's signature describes, as source: `!call [get, x, key[, default]]`.
pub(crate) fn call_form(found: &builtins::Descriptor) -> String {
    format!("!call [{}, {}]", found.name, found.signature)
}

fn builtin(found: &builtins::Descriptor, json: bool) -> ExitCode {
    let name = found.name;
    let call = call_form(found);
    let mentions = mentions(name);
    if json {
        let rows: Vec<_> = mentions.iter().map(|(doc, section, text)| json!({"document": doc, "section": section, "text": text})).collect();
        super::source::print_report(&json!({"name": name, "family": found.family(), "signature": found.signature, "types": found.types, "summary": found.summary, "min": found.arity.0, "max": found.arity.1, "call": call, "mentions": rows}));
        return ExitCode::SUCCESS;
    }
    println!("{name}: {}\n{} builtin, {} arguments\ncall: `{call}`\ntypes: {}", found.summary, found.family(), arity(found.arity), found.types);
    if mentions.is_empty() {
        println!("\nnot described further in the embedded references; search them with `helix docs`");
        return ExitCode::SUCCESS;
    }
    println!();
    for (document, section, text) in &mentions {
        println!("[{document} {section}] {text}");
    }
    let (document, section, _) = &mentions[0];
    println!("\nnext: `helix docs {document} {section}` for the surrounding rules");
    ExitCode::SUCCESS
}

/// Sentences and table rows of the references that name `name` as code, with their section.
fn mentions(name: &str) -> Vec<(String, String, String)> {
    let written = [format!("`{name}`"), format!("[{name},"), format!("[{name}]"), format!("`{name}(")];
    let names = |text: &str| written.iter().any(|w| text.contains(w.as_str()));
    let mut found = Vec::new();
    for document in embedded() {
        let mut fenced = false;
        let mut current = String::from("intro");
        let mut paragraph = String::new();
        let flush = |paragraph: &mut String, current: &str, found: &mut Vec<(String, String, String)>| {
            for sentence in sentences(paragraph).filter(|s| names(s)) {
                found.push((document.name.clone(), current.to_string(), clip(sentence)));
            }
            paragraph.clear();
        };
        for (i, line) in document.lines.iter().enumerate() {
            if line.starts_with("```") {
                fenced = !fenced;
                flush(&mut paragraph, &current, &mut found);
            } else if fenced {
            } else if let Some(section) = document.sections.iter().find(|s| s.start == i) {
                flush(&mut paragraph, &current, &mut found);
                current = section.slug.clone();
            } else if line.trim().is_empty() || line.starts_with('|') {
                flush(&mut paragraph, &current, &mut found);
                // Table rows stand alone; the separator row names nothing.
                if line.starts_with('|') && names(line) {
                    found.push((document.name.clone(), current.clone(), clip(line.trim())));
                }
            } else {
                paragraph.push(' ');
                paragraph.push_str(line.trim());
            }
        }
        flush(&mut paragraph, &current, &mut found);
    }
    found.truncate(MENTIONS);
    found
}

/// Sentences of a paragraph, split after `. ` outside code spans.
fn sentences(paragraph: &str) -> impl Iterator<Item = &str> {
    let mut code = false;
    let mut start = 0;
    let mut cuts = Vec::new();
    let bytes = paragraph.as_bytes();
    for (i, &b) in bytes.iter().enumerate() {
        match b {
            b'`' => code = !code,
            b'.' if !code && bytes.get(i + 1) == Some(&b' ') => {
                cuts.push(&paragraph[start..=i]);
                start = i + 2;
            }
            _ => {}
        }
    }
    cuts.push(&paragraph[start.min(paragraph.len())..]);
    cuts.into_iter().map(str::trim).filter(|s| !s.is_empty())
}

fn unknown_builtin(name: &str) -> ExitCode {
    let modules: Vec<_> = library::exports().iter().filter(|(_, exports)| exports.iter().any(|e| e == name)).map(|(m, _)| *m).collect();
    if let Some(module) = modules.first() {
        eprintln!("`{name}` is not a builtin; it is exported by std:{module}: see `helix std {module}.{name}`");
        return ExitCode::from(2);
    }
    unknown("builtin", name, builtins::names(), "list builtins with `helix builtins`")
}
