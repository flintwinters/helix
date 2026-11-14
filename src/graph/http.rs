//! Pure startup descriptor checks shared by evaluation, restoration and supervision.
use crate::model::{Value, Function, string as s};
use std::{sync::Arc, net::SocketAddr};

pub(crate) fn validate_function(function: &Function, parameters: usize) -> Result<(), String> {
    let found = function.program.graph.parameters(function.node).map_err(|error| error.message)?;
    if found.len() != parameters { return Err(format!("supervised function requires {parameters} parameters")); }
    Ok(())
}

pub(crate) fn validate_options(options: &Value) -> Result<(), String> {
    let Value::Hash(fields) = options else { return Err("HTTP options must be a map".into()) };
    let Some(Value::String(bind)) = fields.get(&s("bind")) else { return Err("HTTP options require bind".into()) };
    let address: SocketAddr = bind.parse().map_err(|_| "bind must be a numeric loopback address")?;
    if !address.ip().is_loopback() { return Err("only numeric loopback listeners are supported".into()); }
    for (key, value) in fields.iter() {
        match key.as_str() {
            Some("bind") => {},
            Some("requests") if matches!(value, Value::Integer(n) if *n > 0) => {},
            Some("promise") => crate::builtins::typing::promise::validate(value)?,
            Some("actors") => validate_actors(value)?,
            _ => return Err("unknown or invalid HTTP option".into()),
        }
    }
    Ok(())
}

pub(crate) struct Definition {
    pub(crate) initial: Value,
    pub(crate) receive: Arc<Function>,
    pub(crate) initialize: Option<Arc<Function>>,
    pub(crate) promise: Option<Value>,
}
pub(crate) fn definition(value: &Value) -> Result<Definition, String> {
    let Value::Hash(fields) = value else { return Err("actor definition must be a map".into()) };
    if fields.iter().any(|(key,_)| !matches!(key.as_str(),Some("initial" | "receive" | "initialize" | "promise"))) {
        return Err("unknown actor definition field".into());
    }
    let initial = fields.get(&s("initial")).filter(|v| v.plain_yaml().is_some()).cloned().ok_or("actor requires ordinary initial state")?;
    let Some(Value::Function(receive)) = fields.get(&s("receive")) else { return Err("actor receive must be a function".into()) };
    validate_function(receive,2)?;
    let initialize = match fields.get(&s("initialize")) {
        Some(Value::Function(function)) => Some(function.clone()),
        None => None,
        _ => return Err("actor initialize must be a function".into()),
    };
    if let Some(initialize) = &initialize { validate_function(initialize,1)?; }
    let promise = fields.get(&s("promise")).cloned();
    if let Some(promise) = &promise { crate::builtins::typing::promise::validate(promise)?; }
    Ok(Definition {initial,receive:receive.clone(),initialize,promise})
}
fn validate_actors(value: &Value) -> Result<(), String> {
    let Value::Hash(definitions) = value else { return Err("actors must be a map".into()) };
    for (name,value) in definitions.iter() {
        if name.as_str() == Some("") { return Err("actor name must be nonempty".into()); }
        definition(value)?;
    }
    Ok(())
}
