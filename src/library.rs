//! The standard library: modules written in Helix, embedded in the binary and imported as `std:NAME`.

use crate::graph::{Syntax, ROOT};

/// `(name, source)` of every standard module.
const MODULES: &[(&str, &str)] = &[("source", include_str!("../std/source.yaml")), ("search", include_str!("../std/search.yaml")), ("actor", include_str!("../std/actor.yaml")), ("http", include_str!("../std/http.yaml")), ("facts", include_str!("../std/facts.yaml")), ("nginx", include_str!("../std/nginx.yaml")), ("linux", include_str!("../std/linux.yaml")), ("helper", include_str!("../std/helper.yaml")), ("ssh", include_str!("../std/ssh.yaml")), ("sqlite", include_str!("../std/sqlite.yaml")), ("convergence", include_str!("../std/convergence.yaml")), ("type_descriptor", include_str!("../std/type_descriptor.yaml")), ("command", include_str!("../std/command.yaml")), ("provider", include_str!("../std/provider.yaml")), ("orchestration", include_str!("../std/orchestration.yaml")), ("inventory", include_str!("../std/inventory.yaml")), ("fuzz", include_str!("../std/fuzz.yaml")), ("validation", include_str!("../std/validation.yaml")), ("dependency", include_str!("../std/dependency.yaml")), ("text", include_str!("../std/text.yaml")), ("diff", include_str!("../std/diff.yaml")), ("json", include_str!("../std/json.yaml")), ("csv", include_str!("../std/csv.yaml")), ("set", include_str!("../std/set.yaml")), ("stack", include_str!("../std/stack.yaml")), ("queue", include_str!("../std/queue.yaml")), ("yaml", include_str!("../std/yaml.yaml")), ("math", include_str!("../std/math.yaml")), ("list", include_str!("../std/list.yaml")), ("group", include_str!("../std/group.yaml")), ("map", include_str!("../std/map.yaml")), ("types", include_str!("../std/types.yaml")), ("test", include_str!("../std/test.yaml")), ("machine", include_str!("../std/machine.yaml"))];

/// The source of the standard module `name`.
pub fn source(name: &str) -> Option<&'static str> {
    MODULES.iter().find(|(n, _)| *n == name).map(|&(_, src)| src)
}

/// Every standard module name, in documentation order.
pub fn names() -> impl Iterator<Item = &'static str> {
    MODULES.iter().map(|&(name, _)| name)
}

/// `(module, top-level export names)` of every standard module, read once from the embedded
/// sources. Only diagnostics consult it; a module whose root is not a map exports nothing.
pub(crate) fn exports() -> &'static [(&'static str, Vec<String>)] {
    static EXPORTS: std::sync::OnceLock<Vec<(&'static str, Vec<String>)>> = std::sync::OnceLock::new();
    EXPORTS.get_or_init(|| {
        MODULES
            .iter()
            .map(|&(name, src)| {
                let keys = crate::graph::Graph::parse(src).map_or_else(|_| Vec::new(), |graph| match graph.syntax(ROOT) {
                    Syntax::Mapping(entries) => entries.iter().map(|(key, _)| key.clone()).collect(),
                    _ => Vec::new(),
                });
                (name, keys)
            })
            .collect()
    })
}
