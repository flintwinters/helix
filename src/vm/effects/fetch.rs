//! Outbound `http` requests: validated method, headers and body rendered as a curl config on stdin.
//! Header and body bytes never reach argv, where other local users could read them.
use yaml_rust2::Yaml;
use super::{Failure, fault, text, LIMIT};

const METHODS: [&str; 5] = ["GET", "POST", "PUT", "PATCH", "DELETE"];
/// Headers that would change framing, routing or the granted origin rather than describe the request.
const RESERVED: [&str; 7] = ["host", "content-length", "transfer-encoding", "connection", "expect", "te", "upgrade"];

pub(super) struct Request<'a> {
    pub url: &'a str,
    /// curl config for stdin; empty for a plain GET.
    pub config: String,
}

/// Payload: an absolute URL string (GET), or `{url, method?, headers?, body?}`.
pub(super) fn parse(payload: &Yaml) -> Result<Request<'_>, Failure> {
    if let Yaml::String(url) = payload { return Ok(Request { url, config: String::new() }); }
    if payload.as_hash().is_none() { return Err(fault("type", "http payload must be a URL string or {url, method?, headers?, body?}")); }
    if let Some((key, _)) = payload.as_hash().into_iter().flatten()
        .find(|(key, _)| !matches!(key.as_str(), Some("url" | "method" | "headers" | "body"))) {
        return Err(fault("type", &format!("unknown http request field {key:?}; expected url, method, headers or body")));
    }
    let url = text(&payload["url"])?;
    let method = match &payload["method"] {
        Yaml::BadValue | Yaml::Null => "GET",
        Yaml::String(method) if METHODS.contains(&method.as_str()) => method,
        _ => return Err(fault("type", &format!("http method must be one of {}", METHODS.join(", ")))),
    };
    let mut config = String::new();
    if method != "GET" { config.push_str(&format!("request = \"{method}\"\n")); }
    match &payload["headers"] {
        Yaml::BadValue | Yaml::Null => {}
        Yaml::Hash(headers) => for (name, value) in headers {
            let (Some(name), Some(value)) = (name.as_str(), value.as_str()) else {
                return Err(fault("type", "http headers must map string names to string values"));
            };
            let lowered = name.to_ascii_lowercase();
            if name.is_empty() || !name.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_') {
                return Err(fault("type", &format!("invalid http header name {name:?}; use letters, digits, '-' and '_'")));
            }
            if RESERVED.contains(&lowered.as_str()) || lowered.starts_with("proxy-") {
                return Err(fault("capability", &format!("http header {name} is managed by the host and cannot be set")));
            }
            // An empty value would make curl delete the header instead of sending it.
            if value.is_empty() || value.chars().any(|c| c.is_control() && c != '\t') {
                return Err(fault("type", &format!("http header {name} value must be non-empty without control characters")));
            }
            config.push_str(&format!("header = \"{}\"\n", escape(&format!("{name}: {value}"))));
        },
        _ => return Err(fault("type", "http headers must map string names to string values")),
    }
    match &payload["body"] {
        Yaml::BadValue | Yaml::Null => {}
        Yaml::String(_) if method == "GET" => return Err(fault("type", "http GET cannot carry a body; set method to POST, PUT, PATCH or DELETE")),
        Yaml::String(body) if body.len() > LIMIT => return Err(fault("resource", "http body exceeds 1 MiB")),
        Yaml::String(body) if body.contains('\0') => return Err(fault("type", "http body cannot contain NUL")),
        // data-raw never treats a leading '@' as a file to upload.
        Yaml::String(body) => config.push_str(&format!("data-raw = \"{}\"\n", escape(body))),
        _ => return Err(fault("type", "http body must be a string")),
    }
    Ok(Request { url, config })
}

/// Quote for a curl config value: backslash, quote and the line-structure characters.
fn escape(value: &str) -> String {
    let mut out = String::with_capacity(value.len());
    for c in value.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c => out.push(c),
        }
    }
    out
}
