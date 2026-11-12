//! Data interchange actors share string-input and ordinary-data output boundaries.
use super::fault::Fault;
use crate::{emit, value::Value};

mod csv;
mod json;
mod yaml;

pub(super) fn apply(actor: &str, value: &Value) -> Result<Value, Fault> {
    match actor {
        "yaml_dump" | "json_dump" => {
            let data = value
                .plain_yaml()
                .ok_or_else(|| Fault::expected(0, "ordinary data", value))?;
            if actor == "yaml_dump" {
                Ok(Value::String(emit::document(&data)))
            } else {
                emit::json_document(&data)
                    .map(Value::String)
                    .map_err(|message| Fault::at(0, "type", message.into()))
            }
        }
        "csv_dump" => csv::dump(value).map(Value::String),
        _ => {
            let Value::String(text) = value else {
                return Err(Fault::expected(0, "string", value));
            };
            match actor {
                "yaml_parse" => yaml::parse(text),
                "json_parse" => json::parse(text),
                "csv_parse" => csv::parse(text),
                _ => unreachable!("data conversion actor"),
            }
        }
    }
}
