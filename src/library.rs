//! The standard library: modules written in Helix, embedded in the binary and imported as `std:NAME`.

/// `(name, source)` of every standard module.
const MODULES: &[(&str, &str)] = &[("fuzz", include_str!("../std/fuzz.yaml")), ("validation", include_str!("../std/validation.yaml")), ("dependency", include_str!("../std/dependency.yaml")), ("text", include_str!("../std/text.yaml")), ("diff", include_str!("../std/diff.yaml")), ("json", include_str!("../std/json.yaml")), ("csv", include_str!("../std/csv.yaml")), ("set", include_str!("../std/set.yaml")), ("stack", include_str!("../std/stack.yaml")), ("queue", include_str!("../std/queue.yaml")), ("yaml", include_str!("../std/yaml.yaml")), ("math", include_str!("../std/math.yaml")), ("list", include_str!("../std/list.yaml")), ("group", include_str!("../std/group.yaml")), ("map", include_str!("../std/map.yaml")), ("types", include_str!("../std/types.yaml")), ("test", include_str!("../std/test.yaml")), ("machine", include_str!("../std/machine.yaml"))];

/// The source of the standard module `name`.
pub fn source(name: &str) -> Option<&'static str> {
    MODULES.iter().find(|(n, _)| *n == name).map(|&(_, src)| src)
}

/// Every standard module name, in documentation order.
pub fn names() -> impl Iterator<Item = &'static str> {
    MODULES.iter().map(|&(name, _)| name)
}
