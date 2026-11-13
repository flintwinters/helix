//! Explicit, versioned wire codecs. Node references are source paths, never arena indices.
use super::*;
use std::path::PathBuf;

pub(super) trait Wire: Sized {
    fn put(&self, e: &mut Encoder) -> R<J>;
    fn get(j: &J, d: &mut Decoder) -> R<Self>;
}
macro_rules! scalar {
    ($t:ty) => {
        impl Wire for $t {
            fn put(&self, _: &mut Encoder) -> R<J> {
                Ok(serde_json::json!(self))
            }
            fn get(j: &J, _: &mut Decoder) -> R<Self> {
                serde_json::from_value(j.clone())
                    .map_err(|_| format!("expected {}", stringify!($t)))
            }
        }
    };
}
scalar!(String);
scalar!(usize);
scalar!(u64);
scalar!(i64);
scalar!(bool);
impl<T: Wire> Wire for Vec<T> {
    fn put(&self, e: &mut Encoder) -> R<J> {
        Ok(J::Array(self.iter().map(|v| e.v(v)).collect::<R<_>>()?))
    }
    fn get(j: &J, d: &mut Decoder) -> R<Self> {
        array(j)?.iter().map(|v| d.v(v)).collect()
    }
}
impl<T: Wire> Wire for Option<T> {
    fn put(&self, e: &mut Encoder) -> R<J> {
        self.as_ref().map(|v| e.v(v)).unwrap_or(Ok(J::Null))
    }
    fn get(j: &J, d: &mut Decoder) -> R<Self> {
        if j.is_null() {
            Ok(None)
        } else {
            Ok(Some(d.v(j)?))
        }
    }
}
impl<T: Wire, E: Wire> Wire for Result<T, E> {
    fn put(&self, e: &mut Encoder) -> R<J> {
        match self {
            Ok(v) => Ok(serde_json::json!({"ok": e.v(v)?})),
            Err(v) => Ok(serde_json::json!({"error": e.v(v)?})),
        }
    }
    fn get(j: &J, d: &mut Decoder) -> R<Self> {
        if let Some(v) = j.get("ok") {
            keys(j, &["ok"])?;
            Ok(Ok(d.v(v)?))
        } else {
            keys(j, &["error"])?;
            Ok(Err(d.v(field(j, "error")?)?))
        }
    }
}
impl Wire for PathBuf {
    fn put(&self, e: &mut Encoder) -> R<J> {
        e.v(&self.to_str().ok_or("non-UTF-8 source path")?.to_owned())
    }
    fn get(j: &J, d: &mut Decoder) -> R<Self> {
        Ok(PathBuf::from(d.v::<String>(j)?))
    }
}
impl Wire for List {
    fn put(&self, e: &mut Encoder) -> R<J> {
        e.v(self.0.as_ref())
    }
    fn get(j: &J, d: &mut Decoder) -> R<Self> {
        Ok(d.v::<Vec<Value>>(j)?.into())
    }
}
impl Wire for Value {
    fn put(&self, e: &mut Encoder) -> R<J> {
        Ok(match self {
            Self::Null => serde_json::json!({"null": true}),
            Self::Boolean(v) => serde_json::json!({"boolean": v}),
            Self::Integer(v) => serde_json::json!({"integer": v}),
            Self::Float(v) => serde_json::json!({"float": v.to_string()}),
            Self::String(v) => serde_json::json!({"string": v}),
            Self::Array(v) => serde_json::json!({"list": e.v(v)?}),
            Self::Hash(v) => {
                let mut map = serde_json::Map::new();
                for (k, v) in v.iter() {
                    map.insert(k.as_str().ok_or("non-string key")?.into(), e.v(v)?);
                }
                serde_json::json!({"map": map})
            }
            Self::Function(v) => serde_json::json!({"function": e.v(v)?}),
            Self::Machine(v) => serde_json::json!({"machine": e.v(v)?}),
        })
    }
    fn get(j: &J, d: &mut Decoder) -> R<Self> {
        let map = j.as_object().ok_or("expected tagged execution value")?;
        if map.len() != 1 {
            return Err("execution value requires exactly one tag".into());
        }
        let (tag, v) = map.iter().next().unwrap();
        Ok(match tag.as_str() {
            "null" if v == &J::Bool(true) => Self::Null,
            "boolean" => Self::Boolean(d.v(v)?),
            "integer" => Self::Integer(d.v(v)?),
            "float" => {
                let v = d
                    .v::<String>(v)?
                    .parse::<f64>()
                    .map_err(|_| "invalid float")?;
                if !v.is_finite() {
                    return Err("non-finite float".into());
                }
                Self::Float(v)
            }
            "string" => Self::String(d.v(v)?),
            "list" => Self::Array(d.v(v)?),
            "map" => {
                let mut map = Hash::new();
                for (k, v) in v.as_object().ok_or("expected map")? {
                    map.insert(string(k), d.v(v)?);
                }
                Self::Hash(map)
            }
            "function" => Self::Function(d.v(v)?),
            "machine" => Self::Machine(d.v(v)?),
            _ => return Err(format!("unknown execution value tag {tag}")),
        })
    }
}
impl Wire for yaml_rust2::Yaml {
    fn put(&self, e: &mut Encoder) -> R<J> {
        e.v(&crate::value::from_host_yaml(self)?)
    }
    fn get(j: &J, d: &mut Decoder) -> R<Self> {
        d.v::<Value>(j)?
            .plain_yaml()
            .ok_or("expected ordinary YAML data".into())
    }
}
impl Wire for Segment {
    fn put(&self, e: &mut Encoder) -> R<J> {
        match self {
            Self::Key(v) => e.v(v),
            Self::Index(v) => e.v(v),
        }
    }
    fn get(j: &J, d: &mut Decoder) -> R<Self> {
        if j.is_string() {
            Ok(Self::Key(d.v(j)?))
        } else {
            Ok(Self::Index(d.v(j)?))
        }
    }
}
impl Wire for Status {
    fn put(&self, _: &mut Encoder) -> R<J> {
        Ok(J::String(self.as_str().into()))
    }
    fn get(j: &J, _: &mut Decoder) -> R<Self> {
        match j.as_str() {
            Some("ready") => Ok(Self::Ready),
            Some("running") => Ok(Self::Running),
            Some("finished") => Ok(Self::Finished),
            Some("error") => Ok(Self::Error),
            Some("signaled") => Ok(Self::Signaled),
            _ => Err("unknown machine status".into()),
        }
    }
}

macro_rules! record {
    ($t:ident { $($f:ident : $codec:ident),* $(,)? }) => { impl Wire for $t {
        fn put(&self, e: &mut Encoder) -> R<J> {
            Ok(serde_json::json!({ $(stringify!($f): e.$codec(&self.$f)?),* }))
        }
        fn get(j: &J, d: &mut Decoder) -> R<Self> {
            keys(j, &[$(stringify!($f)),*])?;
            Ok(Self { $($f: d.$codec(field(j, stringify!($f))?)?),* })
        }
    }};
}
record!(Span { line: v, col: v });
record!(Scope {
    function: n,
    args: v,
    born: v
});
record!(Region {
    site: n,
    mark: v,
    inputs: v
});
record!(Limits {
    max_depth: v,
    max_iterations: v,
    max_work: v,
    max_values: v,
    max_value_size: v,
    max_eval_depth: v
});
record!(Iteration {
    site: n,
    name: v,
    args: ns,
    values: v,
    index: v,
    output: v,
    state: v,
    count: v
});
record!(Atomic {
    site: n,
    nodes: ns,
    operation: v,
    answers: v,
    cause: v,
    predicate_failed: v
});
record!(EffectRequest {
    id: v,
    operation: v,
    payload: v
});
record!(HelixError {
    kind: v,
    phase: phase,
    message: v,
    file: v,
    path: v,
    reference: v,
    span: v,
    data: v,
    calls: v,
    cause: v
});

// The schema is exhaustive over runtime requests. Adding an engine continuation
// requires a deliberate wire-format decision; there is no debug-string fallback.
macro_rules! variants {
    ($t:ident { $( $variant:ident ( $($f:ident : $codec:ident),* ) ),* ;
                 $( $named:ident { $($nf:ident : $nc:ident),* } ),* $(,)? }) => {
        impl Wire for $t {
            fn put(&self, e: &mut Encoder) -> R<J> { match self {
                $(Self::$variant($($f),*) => Ok(serde_json::json!({"kind": stringify!($variant),
                    $(stringify!($f): e.$codec($f)?),*})),)*
                $(Self::$named { $($nf),* } => Ok(serde_json::json!({"kind": stringify!($named),
                    $(stringify!($nf): e.$nc($nf)?),*})),)*
            }}
            fn get(j: &J, d: &mut Decoder) -> R<Self> {
                match field(j, "kind")?.as_str().ok_or("expected continuation kind")? {
                    $(stringify!($variant) => { keys(j, &["kind", $(stringify!($f)),*])?;
                        Ok(Self::$variant($(d.$codec(field(j, stringify!($f))?)?),*)) },)*
                    $(stringify!($named) => { keys(j, &["kind", $(stringify!($nf)),*])?;
                        Ok(Self::$named { $($nf: d.$nc(field(j, stringify!($nf))?)?),* }) },)*
                    name => Err(format!("unknown {} kind {name}", stringify!($t))),
                }
            }
        }
    }
}
variants!(Actor { Builtin(name:v), Function(function:v); });
variants!(Operation { Builtin(name:v, values:v), Violations(value:v, ty:v); });
variants!(Request {
    Eval(node:n), Finish(node:n, depth:v, local:v), Actor(site:n, nodes:ns),
    Apply(site:n, actor:v, nodes:ns, values:v), Call(site:n, function:v, args:v, nodes:ns),
    CallTrace(site:n, function:v), CallArgument(site:n, args:v, nodes:ns), CallResolved(site:n, args:v, nodes:ns),
    ResultAnnotation(body:n, declaration:n, value:v), ResultChecked(body:n, declaration:n, value:v, ty:v),
    Try(site:n), Finally(site:n), Cleanup(outcome:v), Iterate(iteration:v), UntilTest(iteration:v), UntilNext(iteration:v),
    Refine(site:n, nodes:ns, value:v), Refined(site:n, nodes:ns, value:v), Atomic(atomic:v), PredicateEnd(atomic:v, signature:v),
    SignatureOf(site:n, function:v, pure:v), SignatureSlot(function:v, found:v, pure:v);
    AdvanceChild {},
    TypeEnd { previous:v, memo:omemo },
    Project { site:n, name:v, members:v, actor:v },
    Collect { site:n, actor:v, nodes:ns, keys:v, values:v },
    CallEnd { environment:v, program:v },
    BodyEnd { result_type:on, body:n },
    Parameters { site:caller, origin:v, function:v, args:v, index:v, found:v },
    ParameterType { site:caller, origin:v, function:v, args:v, index:v, found:v, annotation:n },
    ParameterChecked { site:caller, origin:v, function:v, args:v, index:v, ty:v, found:v },
    Branch { name:v, args:ns, index:v },
    Iterated { iteration:v, item:v },
    FormValue { site:n, name:v, args:ns, values:v, index:v }
});
