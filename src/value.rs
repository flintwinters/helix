//! Runtime values. Source syntax and YAML encoding have no execution authority here.

pub(crate) use crate::model::{Hash, Value};
use crate::model::{List, MapKey};
use std::sync::Arc;
impl Hash {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn len(&self) -> usize {
        self.0.len()
    }
    pub fn iter(&self) -> impl Iterator<Item = (&Value, &Value)> {
        self.0.iter().map(|(k, v)| (&k.0, v))
    }
    pub fn keys(&self) -> impl Iterator<Item = &Value> {
        self.0.iter().map(|(k, _)| &k.0)
    }
    pub fn values(&self) -> impl Iterator<Item = &Value> {
        self.0.iter().map(|(_, v)| v)
    }
    pub fn get(&self, key: &Value) -> Option<&Value> {
        self.0.get(key.as_str()?)
    }
    pub fn get_mut(&mut self, key: &Value) -> Option<&mut Value> {
        Arc::make_mut(&mut self.0).get_mut(key.as_str()?)
    }
    pub fn contains_key(&self, key: &Value) -> bool {
        self.get(key).is_some()
    }
    pub fn insert(&mut self, key: Value, value: Value) -> Option<Value> {
        if let Some(slot) = self.get_mut(&key) {
            return Some(std::mem::replace(slot, value));
        }
        assert!(key.as_str().is_some(), "runtime map keys are strings");
        Arc::make_mut(&mut self.0).insert(MapKey(key), value)
    }
}
impl FromIterator<(Value, Value)> for Hash {
    fn from_iter<T: IntoIterator<Item = (Value, Value)>>(items: T) -> Self {
        let mut h = Self::new();
        h.extend(items);
        h
    }
}
impl Extend<(Value, Value)> for Hash {
    fn extend<T: IntoIterator<Item = (Value, Value)>>(&mut self, items: T) {
        for (k, v) in items {
            self.insert(k, v);
        }
    }
}
impl IntoIterator for Hash {
    type Item = (Value, Value);
    type IntoIter = std::iter::Map<
        hashlink::linked_hash_map::IntoIter<MapKey, Value>,
        fn((MapKey, Value)) -> (Value, Value),
    >;
    fn into_iter(self) -> Self::IntoIter {
        Arc::unwrap_or_clone(self.0)
            .into_iter()
            .map(|(k, v)| (k.0, v))
    }
}
impl<'a> IntoIterator for &'a Hash {
    type Item = (&'a Value, &'a Value);
    type IntoIter = std::iter::Map<
        hashlink::linked_hash_map::Iter<'a, MapKey, Value>,
        fn((&'a MapKey, &'a Value)) -> (&'a Value, &'a Value),
    >;
    fn into_iter(self) -> Self::IntoIter {
        self.0.iter().map(|(k, v)| (&k.0, v))
    }
}
impl std::borrow::Borrow<str> for MapKey {
    fn borrow(&self) -> &str {
        self.0
            .as_str()
            .expect("map keys are validated at insertion")
    }
}
impl PartialEq for MapKey {
    fn eq(&self, other: &Self) -> bool {
        self.0.as_str() == other.0.as_str()
    }
}
impl Eq for MapKey {}
impl std::hash::Hash for MapKey {
    fn hash<H: std::hash::Hasher>(&self, state: &mut H) {
        std::hash::Hash::hash(std::borrow::Borrow::<str>::borrow(self), state)
    }
}

/// Equality is a bisimulation over code and captured bindings. Allocation addresses only bound
/// the traversal of recursive binding graphs; they never decide language equality.
impl PartialEq for Value {
    fn eq(&self, other: &Self) -> bool {
        match (self, other) {
            (Self::String(a), Self::String(b)) => a == b,
            (Self::Integer(a), Self::Integer(b)) => a == b,
            (Self::Float(a), Self::Float(b)) => a == b,
            (Self::Integer(a), Self::Float(b)) | (Self::Float(b), Self::Integer(a)) => {
                mixed(*a, *b).is_eq()
            }
            (Self::Boolean(a), Self::Boolean(b)) => a == b,
            (Self::Null, Self::Null) => true,
            _ => equal_values(self.clone(), other.clone(), false),
        }
    }
}
impl PartialEq for crate::model::Function {
    fn eq(&self, other: &Self) -> bool {
        if std::ptr::eq(self, other) {
            return true;
        }
        equal_values(
            Value::Function(Arc::new(self.clone())),
            Value::Function(Arc::new(other.clone())),
            false,
        )
    }
}
fn equal_values(left: Value, right: Value, inspection: bool) -> bool {
    use crate::model::{Binding, Function, NodeId};
    enum Pair {
        Values(Value, Value),
        Code(Arc<Function>, NodeId, Arc<Function>, NodeId),
    }
    let mut pending = vec![Pair::Values(left, right)];
    let mut seen = std::collections::HashSet::new();
    let mut seen_objects = std::collections::HashSet::new();
    while let Some(pair) = pending.pop() {
        let (a, b) = match pair {
            Pair::Values(a, b) => (a, b),
            Pair::Code(a, an, b, bn) => {
                if an == bn && Arc::ptr_eq(&a.program, &b.program) && a.environment == b.environment
                {
                    continue;
                }
                if !seen.insert((Arc::as_ptr(&a) as usize, an, Arc::as_ptr(&b) as usize, bn)) {
                    continue;
                }
                if !a.program.graph.same_code(an, &b.program.graph, bn) {
                    return false;
                }
                let links = a.free_bindings(an);
                let other = b.free_bindings(bn);
                if links.len() != other.len() {
                    return false;
                }
                for (name, link) in links {
                    let Some((_, target)) = other.iter().find(|(key, _)| key == &name) else {
                        return false;
                    };
                    match (link, target) {
                        (Binding::Node(n), Binding::Node(m)) => {
                            pending.push(Pair::Code(a.clone(), n, b.clone(), *m))
                        }
                        (Binding::Value(v), Binding::Value(w)) => {
                            pending.push(Pair::Values(v, w.clone()))
                        }
                        _ => return false,
                    }
                }
                continue;
            }
        };
        match (a, b) {
            (Value::Null, Value::Null) => {}
            (Value::Boolean(a), Value::Boolean(b)) if a == b => {}
            (Value::Integer(a), Value::Integer(b)) if a == b => {}
            (Value::Float(a), Value::Float(b))
                if if inspection {
                    a.to_bits() == b.to_bits()
                } else {
                    a == b
                } => {}
            (Value::Integer(a), Value::Float(b)) | (Value::Float(b), Value::Integer(a))
                if !inspection && mixed(a, b).is_eq() => {}
            (Value::String(a), Value::String(b)) if a == b => {}
            (Value::Array(a), Value::Array(b)) if a.len() == b.len() => pending.extend(
                a.iter()
                    .cloned()
                    .zip(b.iter().cloned())
                    .map(|(a, b)| Pair::Values(a, b)),
            ),
            (Value::Hash(a), Value::Hash(b)) if a.len() == b.len() => {
                if inspection && !a.keys().zip(b.keys()).all(|(a, b)| a == b) {
                    return false;
                }
                for (k, v) in a.iter() {
                    let Some(w) = b.get(k) else { return false };
                    pending.push(Pair::Values(v.clone(), w.clone()));
                }
            }
            (Value::Function(a), Value::Function(b)) => {
                if Arc::ptr_eq(&a, &b) {
                    continue;
                }
                if inspection {
                    if a.node != b.node
                        || a.program != b.program
                        || a.environment.len() != b.environment.len()
                    {
                        return false;
                    }
                    if !seen_objects.insert((0, Arc::as_ptr(&a) as usize, Arc::as_ptr(&b) as usize))
                    {
                        continue;
                    }
                    for (a, b) in a.environment.iter().zip(&b.environment) {
                        if a.function != b.function || a.args.len() != b.args.len() {
                            return false;
                        }
                        pending.extend(
                            a.args
                                .iter()
                                .cloned()
                                .zip(b.args.iter().cloned())
                                .map(|(a, b)| Pair::Values(a, b)),
                        );
                    }
                } else {
                    let (an, bn) = (a.node, b.node);
                    pending.push(Pair::Code(a, an, b, bn));
                }
            }
            (Value::Machine(a), Value::Machine(b)) => {
                if Arc::ptr_eq(&a, &b) {
                    continue;
                }
                if inspection {
                    if a.status != b.status
                        || a.checking != b.checking
                        || a.source_file != b.source_file
                    {
                        return false;
                    }
                    match (&a.evaluator, &b.evaluator) {
                        (Some(a), Some(b))
                            if a.program == b.program && a.requests.len() == b.requests.len() => {}
                        (None, None) => {}
                        _ => return false,
                    }
                    if !seen_objects.insert((1, Arc::as_ptr(&a) as usize, Arc::as_ptr(&b) as usize))
                    {
                        continue;
                    }
                    pending.push(Pair::Values(a.inspection(), b.inspection()));
                } else if !a.logical_eq(&b) {
                    return false;
                }
            }
            _ => return false,
        }
    }
    true
}
/// Exact comparison without rounding the integer through f64 (including i64::MAX).
pub(crate) fn mixed(integer: i64, float: f64) -> std::cmp::Ordering {
    use std::cmp::Ordering;
    if float >= 9223372036854775808.0 {
        return Ordering::Less;
    }
    if float < -9223372036854775808.0 {
        return Ordering::Greater;
    }
    match integer.cmp(&(float as i64)) {
        Ordering::Equal => 0.0_f64
            .partial_cmp(&float.fract())
            .unwrap_or(Ordering::Equal),
        order => order,
    }
}
impl Value {
    /// Inspection sharing preserves source identity, scalar kinds and displayed collection order.
    /// Language equality deliberately abstracts some of those presentation details.
    pub(crate) fn same_view(&self, other: &Self) -> bool {
        equal_values(self.clone(), other.clone(), true)
    }
    pub fn as_str(&self) -> Option<&str> {
        if let Self::String(s) = self {
            Some(s)
        } else {
            None
        }
    }
    pub fn from_yaml(value: &yaml_rust2::Yaml) -> Self {
        use yaml_rust2::Yaml as Y;
        match value {
            Y::Null => Self::Null,
            Y::Boolean(v) => Self::Boolean(*v),
            Y::Integer(v) => Self::Integer(*v),
            Y::Real(v) => Self::Float(v.parse().expect("source floats are finite")),
            Y::String(v) => Self::String(v.clone()),
            Y::Array(v) => Self::Array(v.iter().map(Self::from_yaml).collect()),
            Y::Hash(v) => Self::Hash(
                v.iter()
                    .map(|(k, v)| (Self::from_yaml(k), Self::from_yaml(v)))
                    .collect(),
            ),
            _ => unreachable!("unsupported YAML cannot enter runtime"),
        }
    }
    /// Only ordinary data is a recovery literal. Functions and machines require their source.
    pub fn plain_yaml(&self) -> Option<yaml_rust2::Yaml> {
        use yaml_rust2::Yaml as Y;
        Some(match self {
            Self::Null => Y::Null,
            Self::Boolean(v) => Y::Boolean(*v),
            Self::Integer(v) => Y::Integer(*v),
            Self::Float(v) => Y::Real(format!("{v:?}")),
            Self::String(v) => Y::String(v.clone()),
            Self::Array(v) => Y::Array(v.iter().map(Self::plain_yaml).collect::<Option<_>>()?),
            Self::Hash(v) => Y::Hash(
                v.iter()
                    .map(|(k, v)| Some((k.plain_yaml()?, v.plain_yaml()?)))
                    .collect::<Option<_>>()?,
            ),
            Self::Function(_) | Self::Machine(_) => return None,
        })
    }
}

impl From<Vec<Value>> for List {
    fn from(items: Vec<Value>) -> Self {
        Self(Arc::new(items))
    }
}
impl FromIterator<Value> for List {
    fn from_iter<T: IntoIterator<Item = Value>>(items: T) -> Self {
        Self::from(items.into_iter().collect::<Vec<_>>())
    }
}
impl std::ops::Deref for List {
    type Target = Vec<Value>;
    fn deref(&self) -> &Self::Target {
        &self.0
    }
}
impl std::ops::DerefMut for List {
    fn deref_mut(&mut self) -> &mut Self::Target {
        Arc::make_mut(&mut self.0)
    }
}
impl IntoIterator for List {
    type Item = Value;
    type IntoIter = std::vec::IntoIter<Value>;
    fn into_iter(self) -> Self::IntoIter {
        Arc::unwrap_or_clone(self.0).into_iter()
    }
}
impl<'a> IntoIterator for &'a List {
    type Item = &'a Value;
    type IntoIter = std::slice::Iter<'a, Value>;
    fn into_iter(self) -> Self::IntoIter {
        self.0.iter()
    }
}

impl PartialEq for crate::model::Program {
    fn eq(&self, other: &Self) -> bool {
        // Code views are deterministic derived data, not a second source of program identity.
        std::ptr::eq(self, other) || (self.graph == other.graph && self.modules == other.modules)
    }
}
