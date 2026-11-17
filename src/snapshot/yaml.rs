//! YAML snapshot text to a value tree. Snapshots can exceed the source-graph node budget, so
//! they bypass `Graph` and are bounded by the byte, nesting and object budgets instead.
//! Anchors, aliases and tags never occur in written snapshots and are refused, which also
//! rules out alias expansion.
use super::*;
use yaml_rust2::{
    Yaml,
    parser::{Event, MarkedEventReceiver, Parser},
    scanner::{Marker, TScalarStyle},
};

const MAX_DEPTH: usize = 128;

#[derive(Default)]
struct Builder {
    stack: Vec<(J, Option<String>)>,
    root: Option<J>,
    documents: usize,
    error: Option<String>,
}
impl Builder {
    fn fail(&mut self, message: &str) {
        self.error.get_or_insert_with(|| message.to_owned());
    }
    fn open(&mut self, container: J) {
        if self.stack.len() >= MAX_DEPTH {
            return self.fail("snapshot nesting exceeds budget");
        }
        self.stack.push((container, None));
    }
    fn place(&mut self, value: J) {
        let Some((parent, key)) = self.stack.last_mut() else {
            self.root = Some(value);
            return;
        };
        match parent {
            J::Array(items) => items.push(value),
            J::Object(map) => match key.take() {
                Some(key) if map.contains_key(&key) => self.fail("duplicate mapping key"),
                Some(key) => {
                    map.insert(key, value);
                }
                None => match value {
                    J::String(next) => *key = Some(next),
                    _ => self.fail("mapping keys must be strings"),
                },
            },
            _ => unreachable!("only collections are opened"),
        }
    }
    fn scalar(text: &str, style: TScalarStyle) -> Result<J, &'static str> {
        if style != TScalarStyle::Plain {
            return Ok(J::String(text.to_owned()));
        }
        Ok(match Yaml::from_str(text) {
            Yaml::Null => J::Null,
            Yaml::Boolean(b) => J::Bool(b),
            Yaml::Integer(i) => J::from(i),
            Yaml::String(s) => J::String(s),
            _ => return Err("snapshot numbers must be 64-bit integers"),
        })
    }
}
impl MarkedEventReceiver for Builder {
    fn on_event(&mut self, event: Event, _: Marker) {
        if self.error.is_some() {
            return;
        }
        match event {
            Event::Scalar(text, style, anchor, tag) => {
                if anchor != 0 || tag.is_some() {
                    return self.fail("snapshot anchors and tags are unsupported");
                }
                match Self::scalar(&text, style) {
                    Ok(value) => self.place(value),
                    Err(message) => self.fail(message),
                }
            }
            Event::SequenceStart(anchor, tag) | Event::MappingStart(anchor, tag)
                if anchor != 0 || tag.is_some() =>
            {
                self.fail("snapshot anchors and tags are unsupported")
            }
            Event::SequenceStart(..) => self.open(J::Array(Vec::new())),
            Event::MappingStart(..) => self.open(J::Object(Default::default())),
            Event::SequenceEnd | Event::MappingEnd => {
                let (value, _) = self.stack.pop().expect("balanced collection events");
                self.place(value);
            }
            Event::Alias(_) => self.fail("snapshot aliases are unsupported"),
            Event::DocumentStart => {
                self.documents += 1;
                if self.documents > 1 {
                    self.fail("snapshot must be one document");
                }
            }
            _ => {}
        }
    }
}

pub(super) fn parse(text: &str) -> R<J> {
    let mut builder = Builder::default();
    Parser::new_from_str(text)
        .load(&mut builder, true)
        .map_err(|e| format!("invalid snapshot: {e}"))?;
    if let Some(message) = builder.error {
        return Err(format!("invalid snapshot: {message}"));
    }
    builder
        .root
        .ok_or_else(|| "invalid snapshot: empty document".into())
}
