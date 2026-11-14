//! The standard library: modules written in Helix, embedded in the binary and imported as `std:NAME`.

/// `(name, source)` of every standard module.
const MODULES: &[(&str, &str)] = &[("source", include_str!("../std/source/main.yaml")), ("search", include_str!("../std/search/main.yaml")), ("actor", include_str!("../std/actor/main.yaml")), ("http", include_str!("../std/http/main.yaml")), ("facts", include_str!("../std/facts/main.yaml")), ("nginx", include_str!("../std/nginx/main.yaml")), ("ssh", include_str!("../std/ssh/main.yaml")), ("sqlite", include_str!("../std/sqlite/main.yaml")), ("convergence", include_str!("../std/convergence/main.yaml")), ("type_descriptor", include_str!("../std/type_descriptor/main.yaml")), ("command", include_str!("../std/command/main.yaml")), ("provider", include_str!("../std/provider/main.yaml")), ("orchestration", include_str!("../std/orchestration/main.yaml")), ("inventory", include_str!("../std/inventory/main.yaml")), ("fuzz", include_str!("../std/fuzz/main.yaml")), ("validation", include_str!("../std/validation/main.yaml")), ("dependency", include_str!("../std/dependency/main.yaml")), ("text", include_str!("../std/text/main.yaml")), ("diff", include_str!("../std/diff/main.yaml")), ("json", include_str!("../std/json/main.yaml")), ("csv", include_str!("../std/csv/main.yaml")), ("set", include_str!("../std/set/main.yaml")), ("stack", include_str!("../std/stack/main.yaml")), ("queue", include_str!("../std/queue/main.yaml")), ("yaml", include_str!("../std/yaml/main.yaml")), ("math", include_str!("../std/math/main.yaml")), ("list", include_str!("../std/list/main.yaml")), ("group", include_str!("../std/group/main.yaml")), ("map", include_str!("../std/map/main.yaml")), ("types", include_str!("../std/types/main.yaml")), ("test", include_str!("../std/test/main.yaml")), ("machine", include_str!("../std/machine/main.yaml"))];

/// The source of the standard module `name`.
pub fn source(name: &str) -> Option<&'static str> {
    MODULES.iter().find(|(n, _)| *n == name).map(|&(_, src)| src)
}

/// Every standard module name, in documentation order.
pub fn names() -> impl Iterator<Item = &'static str> {
    MODULES.iter().map(|&(name, _)| name)
}
