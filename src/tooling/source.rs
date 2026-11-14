//! Bounded source reflection. Public identities are typed source paths.
use crate::{
    graph::{Effects, Graph, Site, Syntax},
    model::{Modules, Segment},
    value::Value,
};
use serde_json::{Value as J, json};
use yaml_rust2::Yaml;

pub(crate) fn json(yaml: &Yaml) -> J {
    serde_json::from_str(&crate::json_document(yaml).unwrap()).unwrap()
}
pub(crate) fn yaml(value: &J) -> Yaml {
    yaml_rust2::YamlLoader::load_from_str(&value.to_string())
        .unwrap()
        .remove(0)
}
pub(crate) fn reference(graph: &Graph, id: usize, source: &str) -> J {
    json!({"source":graph.source(id).unwrap_or(source), "path": graph.segments(id).into_iter().map(|s| match s {Segment::Key(k)=>json!(k),Segment::Index(i)=>json!(i)}).collect::<Vec<_>>()})
}
pub(crate) fn location(graph: &Graph, id: usize) -> J {
    let span = graph.span(id);
    json!({"line":span.line,"column":span.col})
}
pub(crate) fn reflect(graph: &Graph, source: &str, modules: &Modules) -> Result<J, String> {
    let code = graph.code_views();
    let code_json = json(&code[0].plain_yaml().unwrap());
    let mut evidence_bytes = code_json.to_string().len();
    let mut nodes = Vec::new();
    let mut pending = vec![(0, None)];
    while let Some((id, parent)) = pending.pop() {
        let mut node = json!({"reference":reference(graph,id,source),"location":location(graph,id),"parent":parent.map(|p| json!({"up":graph.segments(id).len()-graph.segments(p).len()}))});
        let mut ancestor = graph.lexical_parent(id);
        while let Some(at) = ancestor {
            if graph.form(at) == Some("fn") {
                break;
            }
            ancestor = graph.lexical_parent(at);
        }
        if matches!(graph.syntax(id), Syntax::Call(_) | Syntax::Reference(_)) {
            node["enclosing_function"] = ancestor
                .map(|at| reference(graph, at, source))
                .unwrap_or(J::Null);
        }
        let (kind, children) = match graph.syntax(id) {
            Syntax::Scalar(_) => ("scalar", vec![]),
            Syntax::Reference(name) => {
                node["name"] = json!(name);
                node["binding"] = binding(graph, id, name, source, modules);
                node["target"] = graph
                    .named_node(id, name, &|n| modules.root_of(n).unwrap_or(n))
                    .map(|n| reference(graph, n, source))
                    .unwrap_or(J::Null);
                ("reference", vec![])
            }
            Syntax::Literal(_) => ("literal", vec![]),
            Syntax::Invalid(message) => {
                node["error"] = json!(message);
                ("invalid", vec![])
            }
            Syntax::Sequence(items) => ("sequence", items.to_vec()),
            Syntax::Mapping(entries) => ("mapping", entries.iter().map(|(_, n)| *n).collect()),
            Syntax::Call(items) => {
                node["actor"] = json!(graph.form(id));
                node["binding"] = if let Some(name) = graph.form(id) {
                    if crate::builtins::arity(name).is_some() {
                        json!({"kind":"builtin","name":name})
                    } else {
                        binding(graph, id, name, source, modules)
                    }
                } else {
                    json!({"kind":"unresolved","reason":"computed callee"})
                };
                node["target"] = graph
                    .identifier(items[0])
                    .filter(|name| crate::builtins::arity(name).is_none())
                    .or_else(|| match graph.syntax(items[0]) {
                        Syntax::Reference(n) => Some(n),
                        _ => None,
                    })
                    .and_then(|name| {
                        graph.named_node(id, name, &|n| modules.root_of(n).unwrap_or(n))
                    })
                    .map(|n| reference(graph, n, source))
                    .unwrap_or(J::Null);
                if graph.form(id) == Some("fn") {
                    node["parameters"]=json!(graph.parameters(id).ok().map(|p|p.into_iter().map(|(n,a)|json!({"name":n,"annotation":a.map(|n|reference(graph,n,source))})).collect::<Vec<_>>()));
                    node["types"] = json(&super::analysis::evidence(graph, id));
                    node["body"] = items
                        .get(2)
                        .map(|&n| reference(graph, n, source))
                        .unwrap_or(J::Null);
                    node["result_annotation"] = items
                        .get(3)
                        .map(|&n| reference(graph, n, source))
                        .unwrap_or(J::Null);
                    let (state, at) = match graph.effects(id, &|n| modules.root_of(n)) {
                        Effects::None => ("none_found", None),
                        Effects::Requests(n) => ("requests", Some(n)),
                        Effects::Unproven(n) => ("unresolved", Some(n)),
                    };
                    node["effects"] = json!({"state":state,"basis":"syntax","at":at.map(|n|reference(graph,n,source)),"location":at.map(|n|location(graph,n))});
                }
                (
                    if graph.form(id) == Some("fn") {
                        "function"
                    } else {
                        "call"
                    },
                    items.to_vec(),
                )
            }
        };
        node["kind"] = json!(kind);
        node["children"] = json!(
            children
                .iter()
                .map(|&n| {
                    let path = reference(graph, n, source);
                    json!(&path["path"].as_array().unwrap()[graph.segments(id).len()..])
                })
                .collect::<Vec<_>>()
        );
        evidence_bytes = evidence_bytes.saturating_add(node.to_string().len());
        if evidence_bytes > super::stream::MAX_INPUT_BYTES as usize / 2 {
            return Err("source reflection exceeds evidence byte limit".into());
        }
        nodes.push(node);
        pending.extend(children.into_iter().rev().map(|n| (n, Some(id))));
    }
    let record = json!({"version":1,"kind":"source","source":source,"reference":{"source":source,"path":[]},"location":{"line":1,"column":1},"code":code_json,"nodes":nodes});
    crate::value::from_host_yaml(&yaml(&record))
        .map_err(|e| format!("source reflection exceeds ordinary-data bounds: {e}"))?;
    Ok(record)
}
pub(crate) fn parse(text: &str) -> Result<Value, crate::model::HelixError> {
    let graph = Graph::parse(text)?;
    let record = reflect(&graph, "<input>", &Modules::default())
        .map_err(|message| graph.error(0, "limit", message))?;
    Ok(Value::from_yaml(&yaml(&record)))
}

fn binding(graph: &Graph, id: usize, name: &str, source: &str, modules: &Modules) -> J {
    match graph.lexical_base(id,name) {
        Some((Site::Parameter(function,index),suffix))=>json!({"kind":"parameter","function":reference(graph,function,source),"index":index,"member":suffix}),
        Some((Site::Entry(_),_))=>graph.named_node(id,name,&|n|modules.root_of(n).unwrap_or(n)).map(|n|json!({"kind":"declaration","target":reference(graph,n,source),"location":location(graph,n)})).unwrap_or(json!({"kind":"unresolved","reason":"dynamic member"})),
        None if crate::builtins::is_primitive(name)=>json!({"kind":"primitive","name":name}),
        None=>json!({"kind":"unresolved","reason":"no lexical declaration"}),
    }
}
