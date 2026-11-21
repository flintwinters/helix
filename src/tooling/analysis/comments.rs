//! Comment retention for canonical formatting. The program graph has no place for comments, so
//! they are recovered from the source text and re-attached to the structural path of the entry
//! they annotate: a standalone comment precedes the next entry, a trailing comment stays with
//! the entry on its line. Paths are raw YAML paths (mapping keys and sequence indices); both
//! documents share the same tags and structure, so a path names the same entry in each.
use crate::model::Segment;
use std::collections::HashMap;
use yaml_rust2::parser::{Event, MarkedEventReceiver, Parser};
use yaml_rust2::scanner::{Marker, Scanner, TScalarStyle, TokenType};

type Path = Vec<Segment>;

/// An entry's source position: its key for mapping values, else its own start.
struct Entry {
    path: Path,
    index: usize,
    line: usize,
}

enum Open {
    Seq(usize),
    Map { key: Option<(String, Marker)> },
}

struct Entries<'a> {
    source: &'a str,
    /// Byte offset of each character; markers count characters.
    bytes: &'a [usize],
    open: Vec<(Open, Path)>,
    found: Vec<Entry>,
}

impl Entries<'_> {
    /// Register the next node and return its path, positioned at its key when it is a map value.
    fn node(&mut self, at: Marker, tagged: bool) -> Path {
        let (path, at) = match self.open.last_mut() {
            None => (Vec::new(), at),
            Some((Open::Seq(next), base)) => {
                *next += 1;
                (extend(base, Segment::Index(*next - 1)), at)
            }
            Some((Open::Map { key }, base)) => {
                let (name, key_at) = key.take().expect("a map value follows its key");
                (extend(base, Segment::Key(name)), key_at)
            }
        };
        let (index, line) = if tagged { self.tag_start(at) } else { (self.bytes[at.index()], at.line()) };
        self.found.push(Entry { path: path.clone(), index, line });
        path
    }

    /// A tagged node's marker follows its tag, possibly on a later line; the entry starts at the tag.
    fn tag_start(&self, at: Marker) -> (usize, usize) {
        let before = self.source[..self.bytes[at.index()]].trim_end();
        let tag = before.rfind(char::is_whitespace).map_or(0, |n| n + 1);
        if !before[tag..].starts_with('!') {
            return (self.bytes[at.index()], at.line());
        }
        (tag, 1 + self.source[..tag].matches('\n').count())
    }
}

fn extend(base: &Path, segment: Segment) -> Path {
    let mut path = base.clone();
    path.push(segment);
    path
}

impl MarkedEventReceiver for Entries<'_> {
    fn on_event(&mut self, event: Event, at: Marker) {
        match event {
            Event::Scalar(text, _, _, tag) => {
                if let Some((Open::Map { key: key @ None }, _)) = self.open.last_mut() {
                    *key = Some((text, at));
                } else {
                    self.node(at, tag.is_some());
                }
            }
            Event::Alias(_) => {
                self.node(at, false);
            }
            Event::SequenceStart(_, tag) => {
                let path = self.node(at, tag.is_some());
                self.open.push((Open::Seq(0), path));
            }
            Event::MappingStart(_, tag) => {
                let path = self.node(at, tag.is_some());
                self.open.push((Open::Map { key: None }, path));
            }
            Event::SequenceEnd | Event::MappingEnd => {
                self.open.pop();
            }
            _ => {}
        }
    }
}

/// Byte offsets by character index, with a final entry for the end of `source`.
fn byte_offsets(source: &str) -> Vec<usize> {
    source.char_indices().map(|(at, _)| at).chain([source.len()]).collect()
}

fn entries(source: &str) -> Vec<Entry> {
    let bytes = byte_offsets(source);
    let mut receiver = Entries { source, bytes: &bytes, open: Vec::new(), found: Vec::new() };
    let _ = Parser::new_from_str(source).load(&mut receiver, false);
    receiver.found
}

/// Byte ranges of scalar content that may contain `#` without starting a comment.
fn protected(source: &str) -> Vec<(usize, usize)> {
    let line_end = |from: usize| source[from..].find('\n').map_or(source.len(), |n| from + n);
    let bytes = byte_offsets(source);
    let mut ranges = Vec::new();
    for token in Scanner::new(source.chars()) {
        let TokenType::Scalar(style, _) = token.1 else { continue };
        let start = bytes[token.0.index()];
        match style {
            TScalarStyle::SingleQuoted | TScalarStyle::DoubleQuoted => {
                let quote = if style == TScalarStyle::SingleQuoted { '\'' } else { '"' };
                let mut chars = source[start + 1..].char_indices().peekable();
                let mut end = source.len();
                while let Some((offset, c)) = chars.next() {
                    if c == '\\' && quote == '"' {
                        chars.next();
                    } else if c == quote && chars.peek().is_some_and(|(_, n)| *n == quote && quote == '\'') {
                        chars.next();
                    } else if c == quote {
                        end = start + 1 + offset + 1;
                        break;
                    }
                }
                ranges.push((start, end));
            }
            TScalarStyle::Literal | TScalarStyle::Folded => {
                // Content is every following line indented deeper than the header's line.
                let header = source[..start].rfind('\n').map_or(0, |n| n + 1);
                let base = indent(&source[header..line_end(header)]);
                let mut end = line_end(start);
                let mut at = (end + 1).min(source.len());
                while at < source.len() {
                    let line = &source[at..line_end(at)];
                    if !line.trim().is_empty() && indent(line) <= base {
                        break;
                    }
                    end = line_end(at);
                    at = (end + 1).min(source.len());
                }
                ranges.push((start, end));
            }
            _ => {}
        }
    }
    ranges
}

fn indent(line: &str) -> usize {
    line.len() - line.trim_start().len()
}

struct Comment {
    index: usize,
    line: usize,
    text: String,
    /// Source code precedes the comment on its line.
    trailing: bool,
}

fn comments(source: &str) -> Vec<Comment> {
    let protected = protected(source);
    let mut found = Vec::new();
    let mut offset = 0;
    for (number, line) in source.split_inclusive('\n').enumerate() {
        let text = line.trim_end_matches(['\n', '\r']);
        let hash = text.char_indices().find(|&(column, c)| {
            c == '#'
                && (column == 0 || text[..column].ends_with(char::is_whitespace))
                && !protected.iter().any(|&(start, end)| start <= offset + column && offset + column < end)
        });
        if let Some((column, _)) = hash {
            found.push(Comment {
                index: offset + column,
                line: number + 1,
                text: text[column..].trim_end().to_owned(),
                trailing: !text[..column].trim().is_empty(),
            });
        }
        offset += line.len();
    }
    found
}

/// `formatted` with every comment of `source` re-attached by structural path.
pub(super) fn retain(source: &str, formatted: &str) -> String {
    let found = comments(source);
    if found.is_empty() {
        return formatted.to_owned();
    }
    let mut before = entries(source);
    before.sort_by_key(|entry| (entry.index, entry.path.len()));
    let after: HashMap<Path, usize> = entries(formatted).into_iter().map(|e| (e.path, e.line)).collect();
    let lines: Vec<&str> = formatted.lines().collect();
    // Comments before any code are file headers; key reordering must not move them.
    let first_code = source
        .lines()
        .scan(0, |offset, line| {
            let start = *offset;
            *offset += line.len() + 1;
            Some((start, line))
        })
        .find(|(_, line)| !line.trim().is_empty() && !line.trim_start().starts_with('#'))
        .map_or(source.len(), |(start, _)| start);
    let mut above: HashMap<usize, Vec<String>> = HashMap::new();
    let mut beside: HashMap<usize, Vec<String>> = HashMap::new();
    for comment in found {
        if !comment.trailing && comment.index < first_code {
            above.entry(0).or_default().push(comment.text);
            continue;
        }
        let anchor = if comment.trailing {
            // The outermost entry starting on the comment's own line.
            before.iter().find(|entry| entry.line == comment.line && entry.index < comment.index)
        } else {
            before.iter().find(|entry| entry.index >= comment.index)
        };
        let target = anchor.and_then(|entry| after.get(&entry.path)).map(|line| line - 1);
        match (comment.trailing, target) {
            (true, Some(line)) => beside.entry(line).or_default().push(comment.text),
            (_, target) => above.entry(target.unwrap_or(lines.len())).or_default().push(comment.text),
        }
    }
    let mut out = String::new();
    for index in 0..=lines.len() {
        let pad = lines.get(index).map_or(0, |line| indent(line));
        for text in above.get(&index).into_iter().flatten() {
            out.push_str(&format!("{}{text}\n", " ".repeat(pad)));
        }
        if let Some(line) = lines.get(index) {
            out.push_str(line);
            for text in beside.get(&index).into_iter().flatten() {
                out.push_str(&format!(" {text}"));
            }
            out.push('\n');
        }
    }
    out
}
