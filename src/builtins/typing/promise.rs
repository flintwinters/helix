//! Named, composable machine promises. Names label obligations, never grant authority.
use crate::{model::string as s, value::Value};
use super::{construct::descriptor, super::Fault};

fn validate_name(value: Option<&Value>) -> Result<(), String> {
    if matches!(value, Some(Value::String(name)) if !name.is_empty()) { Ok(()) }
    else { Err("a promise needs a nonempty name".into()) }
}

pub(crate) fn validate(value: &Value) -> Result<(), String> {
    fn visit(value: &Value, depth: usize, remaining: &mut usize) -> Result<(), String> {
        *remaining = remaining.checked_sub(1).ok_or("promise composition exceeds 4096 obligations")?;
        if depth > 64 { return Err("promise composition exceeds 64 levels".into()); }
        let Value::Hash(map) = value else { return Err("expected a promise descriptor".into()); };
        if map.len() != 3 || map.get(&s("type")) != Some(&s("promise")) {
            return Err("a promise requires exactly type, name and requires".into());
        }
        validate_name(map.get(&s("name")))?;
        let Some(Value::Array(items)) = map.get(&s("requires")) else { return Err("promise requires must be a list".into()); };
        if items.is_empty() { return Err("a promise needs at least one obligation".into()); }
        for item in items {
            match item {
                Value::String(name) if name == "reversible" => {
                    *remaining = remaining.checked_sub(1).ok_or("promise composition exceeds 4096 obligations")?;
                },
                Value::String(name) => return Err(format!("unsupported promise obligation `{name}`")),
                Value::Hash(fields) if fields.contains_key(&s("effects")) => {
                    *remaining = remaining.checked_sub(1).ok_or("promise composition exceeds 4096 obligations")?;
                    let Some(Value::Array(operations)) = fields.get(&s("effects")) else {
                        return Err("promise effects must be a list of nonempty operation names".into());
                    };
                    if fields.len() != 1 || operations.iter().any(|op| !matches!(op, Value::String(name) if !name.is_empty())) {
                        return Err("promise effects requires exactly a list of nonempty operation names".into());
                    }
                },
                _ => visit(item, depth + 1, remaining)?,
            }
        }
        Ok(())
    }
    visit(value, 0, &mut 4096)
}

pub(super) fn apply(actor: &str, args: &[Value]) -> Result<Value, Fault> {
    if actor == "promise" {
        validate_name(Some(&args[0])).map_err(|why| Fault::at(0, "type", why))?;
        let result = descriptor("promise", [("name", args[0].clone()), ("requires", args[1].clone())]);
        validate(&result).map_err(|why| Fault::at(1, "type", why))?;
        return Ok(result);
    }
    let Value::Machine(machine) = &args[0] else { return Err(Fault::expected(0, "a vm", &args[0])); };
    validate(&args[1]).map_err(|why| Fault::at(1, "type", why))?;
    machine.with_promise_value(args[1].clone()).map(|machine| Value::Machine(std::sync::Arc::new(machine)))
        .map_err(|why| Fault::at(0, "promise", why))
}

/// Composing a promise retains each named parent obligation.
pub(crate) fn includes(composed: &Value, required: &Value) -> bool {
    if composed.same_view(required) { return true; }
    let Value::Hash(map) = composed else { return false; };
    let Some(Value::Array(parents)) = map.get(&s("requires")) else { return false; };
    parents.iter().any(|parent| includes(parent, required))
}

/// Every composed restriction applies; names do not confer host authority.
pub(crate) fn allows(value: &Value, operation: &str) -> bool {
    let Value::Hash(map) = value else { return false };
    if let Some(Value::Array(operations)) = map.get(&s("effects")) {
        return operations.iter().any(|op| op == &s(operation));
    }
    let Some(Value::Array(items)) = map.get(&s("requires")) else { return false };
    items.iter().all(|item| match item {
        Value::String(name) if name == "reversible" => true,
        other => allows(other, operation),
    })
}

pub(crate) fn reversible(value: &Value) -> bool {
    let Value::Hash(map) = value else { return false };
    let Some(Value::Array(items)) = map.get(&s("requires")) else { return false };
    items.iter().any(|item| item == &s("reversible") || reversible(item))
}
