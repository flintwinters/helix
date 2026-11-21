//! Bounded regex observations; selection and matching policy live in Helix.
use super::Fault;
use crate::value::Value;
use serde_json::json;
pub(super) fn apply(actor: &str, args: &[Value]) -> Result<Value, Fault> {
    if actor == "source_subtype" {
        let left = super::Ty::parse(&args[0]).map_err(|e| Fault::at(0, "type", e))?;
        let right = super::Ty::parse(&args[1]).map_err(|e| Fault::at(1, "type", e))?;
        return Ok(Value::Boolean(left.below(&right)));
    }
    let Value::String(pattern) = &args[0] else {
        return Err(Fault::expected(0, "string", &args[0]));
    };
    let Value::String(text) = &args[1] else {
        return Err(Fault::expected(1, "string", &args[1]));
    };
    if pattern.len() > 16_384 || text.len() > crate::model::MAX_VALUE_SIZE {
        return Err(Fault::whole("limit", "regex input exceeds bounds".into()));
    }
    let re = compiled(pattern)?;
    if actor == "regex_replace" {
        return replace(&re, text, &args[2]);
    }
    let mut matches = Vec::new();
    for captures in re.captures_iter(text) {
        if matches.len() >= 4096 {
            return Err(Fault::whole("limit", "regex exceeds 4096 matches".into()));
        }
        matches.push(json!({"span":captures.get(0).map(|m|vec![m.start(),m.end()]),"captures":captures.iter().map(|m|m.map(|m|json!({"text":m.as_str(),"span":[m.start(),m.end()]}))).collect::<Vec<_>>()}));
    }
    crate::value::from_host_yaml(&crate::tooling::source::yaml(&json!(matches)))
        .map_err(|e| Fault::whole("limit", e))
}

/// Replace every non-overlapping match; `$1`, `${name}` and `$$` in the replacement follow the
/// `regex` crate. Match count and output size stay bounded like `regex_find`.
fn replace(re: &regex::Regex, text: &str, replacement: &Value) -> Result<Value, Fault> {
    let Value::String(replacement) = replacement else {
        return Err(Fault::expected(2, "string", replacement));
    };
    if replacement.len() > 16_384 {
        return Err(Fault::at(2, "limit", "regex replacement exceeds 16 KiB".into()));
    }
    let (mut out, mut last) = (String::new(), 0);
    for (count, captures) in re.captures_iter(text).enumerate() {
        if count >= 4096 {
            return Err(Fault::whole("limit", "regex exceeds 4096 matches".into()));
        }
        let whole = captures.get(0).expect("group zero always participates");
        out.push_str(&text[last..whole.start()]);
        captures.expand(replacement, &mut out);
        last = whole.end();
        if out.len() > crate::model::MAX_VALUE_SIZE {
            return Err(Fault::whole("limit", "regex replacement result exceeds the value size limit".into()));
        }
    }
    out.push_str(&text[last..]);
    if out.len() > crate::model::MAX_VALUE_SIZE {
        return Err(Fault::whole("limit", "regex replacement result exceeds the value size limit".into()));
    }
    Ok(Value::String(out.into()))
}

/// Compilation is a pure function of the pattern, so successful compiles are reused;
/// the bounded per-thread cache is cleared when full rather than tracking recency.
fn compiled(pattern: &str) -> Result<regex::Regex, Fault> {
    thread_local! {
        static CACHE: std::cell::RefCell<std::collections::HashMap<String, regex::Regex>> = Default::default();
    }
    if let Some(re) = CACHE.with_borrow(|cache| cache.get(pattern).cloned()) {
        return Ok(re);
    }
    let re = regex::RegexBuilder::new(pattern)
        .size_limit(1_000_000)
        .dfa_size_limit(1_000_000)
        .nest_limit(64)
        .build()
        .map_err(|e| {
            Fault::at(
                0,
                if matches!(&e, regex::Error::CompiledTooBig(_)) {
                    "limit"
                } else {
                    "regex"
                },
                e.to_string(),
            )
        })?;
    CACHE.with_borrow_mut(|cache| {
        if cache.len() >= 64 {
            cache.clear();
        }
        cache.insert(pattern.to_owned(), re.clone());
    });
    Ok(re)
}
