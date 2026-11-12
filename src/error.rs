//! Structured errors: the YAML shape of an error is part of the public contract.

use crate::value::Value;
use crate::value::Hash;



pub(crate) use crate::model::HelixError;
impl HelixError {
    pub fn to_value(&self) -> Value {
        let mut h = Hash::new();
        h.insert(s("kind"), s(&self.kind));
        h.insert(s("message"), s(&self.message));
        if let Some(file) = &self.file {
            h.insert(s("file"), s(file));
        }
        if let Some(path) = &self.path {
            h.insert(s("path"), s(path));
        }
        if let Some(reference)=&self.reference {
            h.insert(s("reference"),Value::Array(reference.iter().map(|part|match part {crate::model::Segment::Key(key)=>s(key),crate::model::Segment::Index(index)=>Value::Integer(*index as i64)}).collect()));
        }
        h.insert(s("line"), Value::Integer(self.span.line as i64));
        h.insert(s("column"), Value::Integer(self.span.col as i64));
        if let Some(data) = &self.data {
            h.insert(s("data"), data.clone());
        }
        if !self.calls.is_empty() {
            h.insert(s("calls"), Value::Array(self.calls.clone().into()));
        }
        if let Some(cause) = &self.cause { h.insert(s("cause"), cause.clone()); }
        Value::Hash(h)
    }
}

pub(crate) fn s(text: &str) -> Value {
    Value::String(text.to_string())
}
