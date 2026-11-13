//! Versioned executable snapshots, separate from lossy inspection documents.
use crate::model::*;
use crate::value::Hash;
use serde_json::Value as J;
use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;
mod codec;
mod graph;
mod state;
pub(crate) mod validate;
use codec::Wire;
type R<T> = Result<T, String>;
const VERSION: u64 = 1;
const MAX_BYTES: usize = 32 * 1024 * 1024;
const MAX_OBJECTS: usize = 100_000;
const MAX_DEPTH: usize = 128;

fn field<'a>(j: &'a J, name: &str) -> R<&'a J> {
    j.get(name)
        .ok_or_else(|| format!("missing snapshot field {name}"))
}
fn array(j: &J) -> R<&Vec<J>> {
    j.as_array().ok_or("expected snapshot list".into())
}
fn keys(j: &J, names: &[&str]) -> R<()> {
    let map = j.as_object().ok_or("expected snapshot record")?;
    if map.len() != names.len() || names.iter().any(|name| !map.contains_key(*name)) {
        return Err(format!(
            "snapshot record requires fields {}",
            names.join(", ")
        ));
    }
    Ok(())
}

struct Encoder {
    program: Option<Arc<Program>>,
    caller: Option<Arc<Program>>,
    objects: BTreeMap<String, J>,
    identities: HashMap<(&'static str, usize), String>,
    depth: usize,
    units: usize,
}
impl Encoder {
    fn v<T: Wire>(&mut self, v: &T) -> R<J> {
        if self.depth >= MAX_DEPTH || self.units >= MAX_BYTES {
            return Err("snapshot encoding budget exhausted".into());
        }
        self.depth += 1;
        self.units += 32;
        let result = v.put(self);
        self.depth -= 1;
        result
    }
    fn object<T>(
        &mut self,
        kind: &'static str,
        v: &Arc<T>,
        encode: impl FnOnce(&T, &mut Self) -> R<J>,
    ) -> R<J> {
        let identity = (kind, Arc::as_ptr(v) as usize);
        if let Some(name) = self.identities.get(&identity) {
            return Ok(J::String(name.clone()));
        }
        if self.identities.len() >= MAX_OBJECTS {
            return Err("too many snapshot objects".into());
        }
        let name = format!("{kind}-{}", self.identities.len());
        self.identities.insert(identity, name.clone());
        let value = encode(v, self)?;
        self.objects.insert(
            name.clone(),
            serde_json::json!({"kind":kind, "value":value}),
        );
        Ok(J::String(name))
    }
    fn account(&mut self, size: usize) -> R<()> {
        self.units = self.units.saturating_add(size);
        if self.units > MAX_BYTES {
            return Err("snapshot encoding budget exhausted".into());
        }
        Ok(())
    }
    fn location(&mut self, graph: &Graph, node: NodeId) -> R<J> {
        let reference = graph::reference(graph, node)?;
        self.account(reference.to_string().len())?;
        Ok(reference)
    }
    fn n(&mut self, n: &NodeId) -> R<J> {
        let p = self.program.clone().ok_or("node without source program")?;
        self.location(&p.graph, *n)
    }
    fn caller(&mut self, n: &NodeId) -> R<J> {
        let p = self
            .caller
            .clone()
            .or(self.program.clone())
            .ok_or("call without source program")?;
        self.location(&p.graph, *n)
    }
    fn ns(&mut self, ns: &Vec<NodeId>) -> R<J> {
        Ok(J::Array(ns.iter().map(|n| self.n(n)).collect::<R<_>>()?))
    }
    fn on(&mut self, n: &Option<NodeId>) -> R<J> {
        n.as_ref().map(|n| self.n(n)).unwrap_or(Ok(J::Null))
    }
    fn memo(&mut self, memo: &HashMap<NodeId, Value>) -> R<J> {
        let mut entries = memo.iter().collect::<Vec<_>>();
        entries.sort_by_key(|(n, _)| **n);
        Ok(J::Array(
            entries
                .into_iter()
                .map(|(n, v)| Ok(serde_json::json!({"node":self.n(n)?,"value":self.v(v)?})))
                .collect::<R<_>>()?,
        ))
    }
    fn omemo(&mut self, memo: &Option<HashMap<NodeId, Value>>) -> R<J> {
        memo.as_ref().map(|m| self.memo(m)).unwrap_or(Ok(J::Null))
    }
    fn phase(&mut self, phase: &Option<&'static str>) -> R<J> {
        Ok(phase.map(|s| J::String(s.into())).unwrap_or(J::Null))
    }
}

#[derive(Clone)]
enum Object {
    Program(Arc<Program>),
    Function(Arc<Function>),
    Machine(Arc<Vm>),
    Evaluator(Arc<Evaluator>),
}
struct Decoder {
    program: Option<Arc<Program>>,
    caller: Option<Arc<Program>>,
    objects: BTreeMap<String, J>,
    completed: HashMap<String, Object>,
    loading: std::collections::HashSet<String>,
    references: HashMap<usize, HashMap<String, NodeId>>,
    depth: usize,
    units: usize,
}
impl Decoder {
    fn v<T: Wire>(&mut self, j: &J) -> R<T> {
        if self.depth >= MAX_DEPTH || self.units >= MAX_BYTES {
            return Err("snapshot decoding budget exhausted".into());
        }
        self.depth += 1;
        self.units += 1;
        let result = T::get(j, self);
        self.depth -= 1;
        result
    }
    fn object(
        &mut self,
        j: &J,
        kind: &str,
        decode: impl FnOnce(&J, &mut Self) -> R<Object>,
    ) -> R<Object> {
        let name = j.as_str().ok_or("expected object reference")?;
        if let Some(object) = self.completed.get(name) {
            return Ok(object.clone());
        }
        if !self.loading.insert(name.into()) {
            return Err("cyclic executable object references".into());
        }
        let row = self
            .objects
            .get(name)
            .ok_or_else(|| format!("missing object {name}"))?
            .clone();
        keys(&row, &["kind", "value"])?;
        if field(&row, "kind")?.as_str() != Some(kind) {
            return Err(format!("expected {kind} object"));
        }
        let result = decode(field(&row, "value")?, self)?;
        self.loading.remove(name);
        self.completed.insert(name.into(), result.clone());
        Ok(result)
    }
    fn node_in(&mut self, j: &J, p: Arc<Program>) -> R<NodeId> {
        let identity = Arc::as_ptr(&p) as usize;
        if !self.references.contains_key(&identity) {
            let refs = (0..p.graph.nodes.len())
                .map(|n| Ok((reference_key(&graph::reference(&p.graph, n)?)?, n)))
                .collect::<R<_>>()?;
            self.references.insert(identity, refs);
        }
        self.references[&identity]
            .get(&reference_key(j)?)
            .copied()
            .ok_or("unknown source reference".into())
    }
    fn n(&mut self, j: &J) -> R<NodeId> {
        self.node_in(
            j,
            self.program.clone().ok_or("node without source program")?,
        )
    }
    fn caller(&mut self, j: &J) -> R<NodeId> {
        self.node_in(
            j,
            self.caller
                .clone()
                .or(self.program.clone())
                .ok_or("call without source program")?,
        )
    }
    fn ns(&mut self, j: &J) -> R<Vec<NodeId>> {
        array(j)?.iter().map(|n| self.n(n)).collect()
    }
    fn on(&mut self, j: &J) -> R<Option<NodeId>> {
        if j.is_null() {
            Ok(None)
        } else {
            Ok(Some(self.n(j)?))
        }
    }
    fn memo(&mut self, j: &J) -> R<HashMap<NodeId, Value>> {
        let mut memo = HashMap::new();
        for row in array(j)? {
            keys(row, &["node", "value"])?;
            let node = self.n(field(row, "node")?)?;
            let value = self.v(field(row, "value")?)?;
            if memo.insert(node, value).is_some() {
                return Err("duplicate memoized source node".into());
            }
        }
        Ok(memo)
    }
    fn omemo(&mut self, j: &J) -> R<Option<HashMap<NodeId, Value>>> {
        if j.is_null() {
            Ok(None)
        } else {
            Ok(Some(self.memo(j)?))
        }
    }
    fn phase(&mut self, j: &J) -> R<Option<&'static str>> {
        match j.as_str() {
            Some("type") => Ok(Some("type")),
            None if j.is_null() => Ok(None),
            _ => Err("unknown execution phase".into()),
        }
    }
}

impl Vm {
    /// Save complete execution state without executing it or opening imports.
    pub fn snapshot(&self) -> Result<String, String> {
        let mut e = Encoder {
            program: None,
            caller: None,
            objects: BTreeMap::new(),
            identities: HashMap::new(),
            depth: 0,
            units: 0,
        };
        let machine = e.v(&Arc::new(self.clone()))?;
        let doc = serde_json::json!({"format":"helix-execution", "version":VERSION,
            "runtime":env!("CARGO_PKG_VERSION"), "machine":machine, "objects":e.objects});
        let text = serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?;
        if text.len() > MAX_BYTES {
            return Err("snapshot exceeds byte budget".into());
        }
        Ok(text + "\n")
    }
    /// Restore a validated snapshot. This never grants permission to perform effects.
    pub fn restore(snapshot: &str) -> Result<Self, String> {
        if snapshot.len() > MAX_BYTES {
            return Err("snapshot exceeds byte budget".into());
        }
        let doc: J = match serde_json::from_str(snapshot) {
            Ok(doc) => doc,
            Err(_) => {
                let graph = Graph::parse_data(snapshot)
                    .map_err(|e| format!("invalid snapshot: {}", e.message))?;
                let data = graph
                    .data(ROOT)
                    .plain_yaml()
                    .ok_or("snapshot must be ordinary data")?;
                let json = crate::emit::json_document(&data).map_err(str::to_owned)?;
                serde_json::from_str(&json).map_err(|e| format!("invalid snapshot: {e}"))?
            }
        };
        keys(
            &doc,
            &["format", "version", "runtime", "machine", "objects"],
        )?;
        if field(&doc, "format")?.as_str() != Some("helix-execution")
            || field(&doc, "version")?.as_u64() != Some(VERSION)
        {
            return Err("unsupported execution snapshot format/version".into());
        }
        if field(&doc, "runtime")?.as_str() != Some(env!("CARGO_PKG_VERSION")) {
            return Err("unsupported snapshot runtime version".into());
        }
        let objects = field(&doc, "objects")?
            .as_object()
            .ok_or("expected objects map")?;
        if objects.len() > MAX_OBJECTS {
            return Err("too many snapshot objects".into());
        }
        let mut d = Decoder {
            program: None,
            caller: None,
            objects: objects
                .iter()
                .map(|(k, v)| (k.clone(), v.clone()))
                .collect(),
            completed: HashMap::new(),
            loading: Default::default(),
            references: HashMap::new(),
            depth: 0,
            units: 0,
        };
        let machine: Arc<Vm> = d.v(field(&doc, "machine")?)?;
        Ok((*machine).clone())
    }
}

// Object presentation order never changes the identity of a source reference.
fn reference_key(j: &J) -> R<String> {
    keys(j, &["source", "path"])?;
    let source = field(j, "source")?
        .as_str()
        .ok_or("invalid reference source")?;
    let path = array(field(j, "path")?)?;
    if path.len() > MAX_NESTING || path.iter().any(|p| !p.is_string() && p.as_u64().is_none()) {
        return Err("invalid reference path".into());
    }
    Ok(serde_json::json!([source, path]).to_string())
}
