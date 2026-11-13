//! Source topology is embedded as representation nodes with checked path references.
use super::*;
use std::collections::HashSet;
pub(super) fn reference(g: &Graph, n: NodeId) -> R<J> {
    if n >= g.nodes.len() {
        return Err("source node outside graph".into());
    }
    Ok(
        serde_json::json!({"source":g.source(n).unwrap_or("<root>"), "path":g.segments(n).iter().map(|s| match s {
        Segment::Key(k) => J::String(k.clone()), Segment::Index(i) => serde_json::json!(i),
    }).collect::<Vec<_>>()}),
    )
}
fn put_program(p: &Program, e: &mut Encoder) -> R<J> {
    let g = &p.graph;
    let mut rows = Vec::new();
    for (n, node) in g.nodes.iter().enumerate() {
        let kind = match &node.kind {
            Kind::Scalar { value } => serde_json::json!({"scalar":e.v(value)?}),
            Kind::Seq(items) => {
                serde_json::json!({"sequence":items.iter().map(|n| reference(g,*n)).collect::<R<Vec<_>>>()?})
            }
            Kind::Map(items) => {
                serde_json::json!({"mapping":items.iter().map(|(k,n)| Ok(serde_json::json!([k,reference(g,*n)?]))).collect::<R<Vec<_>>>()?})
            }
        };
        rows.push(
            serde_json::json!({"location":reference(g,n)?,"span":e.v(&node.span)?,"kind":kind}),
        );
    }
    let mut links = p.modules.linked.iter().collect::<Vec<_>>();
    links.sort_by_key(|(n, _)| **n);
    let mut policies = BTreeMap::new();
    for (name, n) in &p.modules.policies {
        policies.insert(name, reference(g, *n)?);
    }
    let sorted = |set: &HashSet<usize>| -> R<Vec<J>> {
        let mut ids = set.iter().copied().collect::<Vec<_>>();
        ids.sort_unstable();
        ids.into_iter().map(|n| reference(g, n)).collect()
    };
    Ok(
        serde_json::json!({"nodes":rows,"linked":links.into_iter().map(|(a,b)|Ok(serde_json::json!([reference(g,*a)?,reference(g,*b)?]))).collect::<R<Vec<_>>>()?,
        "policies":policies,"roots":sorted(&p.modules.roots)?,"visible_roots":sorted(&p.modules.visible_roots)?}),
    )
}
fn get_program(j: &J, d: &mut Decoder) -> R<Program> {
    keys(
        j,
        &["nodes", "linked", "policies", "roots", "visible_roots"],
    )?;
    let rows = array(field(j, "nodes")?)?;
    if rows.is_empty() || rows.len() > MAX_NODES {
        return Err("source graph size outside bounds".into());
    }
    let mut lookup = HashMap::new();
    for (n, row) in rows.iter().enumerate() {
        keys(row, &["location", "span", "kind"])?;
        let loc = field(row, "location")?;
        keys(loc, &["source", "path"])?;
        if field(loc, "source")?.as_str().is_none()
            || array(field(loc, "path")?)?.len() > MAX_NESTING
        {
            return Err("invalid source reference".into());
        }
        if lookup.insert(reference_key(loc)?, n).is_some() {
            return Err("duplicate source reference".into());
        }
    }
    let node = |j: &J| -> R<usize> {
        lookup
            .get(&reference_key(j)?)
            .copied()
            .ok_or("missing source reference".into())
    };
    let mut g = Graph::default();
    for row in rows {
        let loc = field(row, "location")?;
        if array(field(loc, "path")?)?.is_empty()
            && field(loc, "source")?.as_str() != Some("<root>")
        {
            g.sources.insert(
                g.nodes.len(),
                field(loc, "source")?.as_str().unwrap().into(),
            );
        }
        let kind = field(row, "kind")?;
        if kind.as_object().is_none_or(|m| m.len() != 1) {
            return Err("invalid source node kind".into());
        }
        let kind = if let Some(value) = kind.get("scalar") {
            let value: yaml_rust2::Yaml = d.v(value)?;
            if matches!(
                value,
                yaml_rust2::Yaml::Array(_) | yaml_rust2::Yaml::Hash(_)
            ) {
                return Err("non-scalar source scalar".into());
            }
            Kind::Scalar { value }
        } else if let Some(items) = kind.get("sequence") {
            Kind::Seq(array(items)?.iter().map(&node).collect::<R<_>>()?)
        } else if let Some(items) = kind.get("mapping") {
            let mut names = HashSet::new();
            let mut pairs = Vec::new();
            for item in array(items)? {
                let pair = array(item)?;
                if pair.len() != 2 {
                    return Err("invalid source mapping entry".into());
                }
                let key = pair[0].as_str().ok_or("non-string source key")?.to_owned();
                if !names.insert(key.clone()) {
                    return Err("duplicate source mapping key".into());
                }
                pairs.push((key, node(&pair[1])?));
            }
            Kind::Map(pairs)
        } else {
            return Err("unknown source node kind".into());
        };
        let span: Span = d.v(field(row, "span")?)?;
        if span.line == 0 || span.col == 0 {
            return Err("source spans are one-based".into());
        }
        g.nodes.push(Node {
            kind,
            parent: None,
            span,
        });
    }
    for n in 0..g.nodes.len() {
        let children = match &g.nodes[n].kind {
            Kind::Seq(v) => v.clone(),
            Kind::Map(v) => v.iter().map(|(_, n)| *n).collect(),
            _ => vec![],
        };
        for child in children {
            // Runtime graph construction is preorder, including grafted modules.
            if child <= n || g.nodes[child].parent.replace(n).is_some() {
                return Err("cyclic or multiply-parented source graph".into());
            }
        }
    }
    for n in 0..g.nodes.len() {
        if n != 0 && g.nodes[n].parent.is_none() && !g.sources.contains_key(&n) {
            return Err("unidentified source root".into());
        }
        // Check the supplied references against topology, not only table membership.
        if reference(&g, n)? != *field(&rows[n], "location")? {
            return Err("source path disagrees with topology".into());
        }
    }
    let roots = array(field(j, "roots")?)?
        .iter()
        .map(&node)
        .collect::<R<HashSet<_>>>()?;
    let visible_roots = array(field(j, "visible_roots")?)?
        .iter()
        .map(&node)
        .collect::<R<HashSet<_>>>()?;
    if roots.iter().any(|n| g.nodes[*n].parent.is_some()) || !visible_roots.is_subset(&roots) {
        return Err("invalid module roots".into());
    }
    let mut linked = HashMap::new();
    for pair in array(field(j, "linked")?)? {
        let pair = array(pair)?;
        if pair.len() != 2 {
            return Err("invalid import link".into());
        }
        let from = node(&pair[0])?;
        let to = node(&pair[1])?;
        if !roots.contains(&to) || linked.insert(from, to).is_some() {
            return Err("invalid or duplicate import link".into());
        }
    }
    let mut policies = HashMap::new();
    for (name, loc) in field(j, "policies")?
        .as_object()
        .ok_or("invalid policies")?
    {
        let n = node(loc)?;
        if !roots.contains(&n) {
            return Err("policy outside module roots".into());
        }
        policies.insert(name.clone(), n);
    }
    let code = g.code_views();
    Ok(Program {
        graph: g,
        code,
        modules: Modules {
            linked,
            policies,
            roots,
            visible_roots,
        },
    })
}
impl Wire for Arc<Program> {
    fn put(&self, e: &mut Encoder) -> R<J> {
        e.object("program", self, put_program)
    }
    fn get(j: &J, d: &mut Decoder) -> R<Self> {
        match d.object(j, "program", |j, d| {
            Ok(Object::Program(Arc::new(get_program(j, d)?)))
        })? {
            Object::Program(p) => Ok(p),
            _ => Err("expected program reference".into()),
        }
    }
}
