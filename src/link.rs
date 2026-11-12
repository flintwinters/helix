//! Static import linking. `[import, SPEC]` names its module with a literal, so every import is
//! resolved when a program loads: the module's file is parsed and grafted into the graph as a
//! detached subtree, once per file however many times it is imported. Loading is therefore
//! deterministic, independent of evaluation order, and reports a missing file or a circular
//! import before anything runs.
//!
//! `SPEC` is `std:NAME` for an embedded standard module, otherwise a path relative to the
//! importing file (or to the working directory for the main program when it is not a file).

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

use yaml_rust2::Yaml;

use crate::error::HelixError;
use crate::graph::{Graph, Kind, NodeId, ROOT};
use crate::library;

/// The modules a program imports: each import form's module root, and which nodes are such roots.
#[derive(Default)]
pub struct Modules {
    linked: HashMap<NodeId, NodeId>,
    roots: HashSet<NodeId>,
    /// File imports are load-time inputs; explicit type regions accept only embedded sources.
    first_file_import: Option<NodeId>,
}

impl Modules {
    pub(crate) fn file_import(&self) -> Option<NodeId> {
        self.first_file_import
    }

    /// The root of the module the `[import, ...]` node `import` loads.
    pub fn root_of(&self, import: NodeId) -> Option<NodeId> {
        self.linked.get(&import).copied()
    }

    /// Whether `id` is a module root. A module's names are its own: it never sees its importer's.
    pub fn is_root(&self, id: NodeId) -> bool {
        self.roots.contains(&id)
    }
}

/// Where relative imports resolve from.
#[derive(Clone)]
enum Origin {
    Dir(PathBuf),
    Std,
}

/// Link every import reachable from the program in `graph`; `main` is the program's own file,
/// when it has one.
pub fn link(graph: &mut Graph, main: Option<&Path>) -> Result<Modules, HelixError> {
    let dir = main.and_then(Path::parent).filter(|d| !d.as_os_str().is_empty()).unwrap_or(Path::new(".")).to_path_buf();
    let mut linker = Linker { graph, modules: Modules::default(), loaded: HashMap::new(), stack: Vec::new() };
    if let Some(main) = main {
        if let Ok(path) = main.canonicalize() {
            linker.stack.push((path.display().to_string(), main.display().to_string()));
        }
    }
    linker.scan(ROOT, &Origin::Dir(dir))?;
    Ok(linker.modules)
}

struct Linker<'g> {
    graph: &'g mut Graph,
    modules: Modules,
    /// Module roots by identity key (canonical path, or `std:NAME`).
    loaded: HashMap<String, NodeId>,
    /// Identity keys of the files being loaded, outermost first: a repeat is a cycle.
    stack: Vec<(String, String)>,
}

impl Linker<'_> {
    /// Link the imports inside the subtree at `tree`, a file living at `origin`.
    fn scan(&mut self, tree: NodeId, origin: &Origin) -> Result<(), HelixError> {
        for import in self.imports(tree) {
            let root = self.load(import, origin)?;
            self.modules.linked.insert(import, root);
        }
        Ok(())
    }

    /// Every `[import, ...]` form in the subtree at `tree`, in document order.
    fn imports(&self, tree: NodeId) -> Vec<NodeId> {
        let mut found = Vec::new();
        let mut pending = vec![tree];
        while let Some(id) = pending.pop() {
            match self.graph.kind(id) {
                Kind::Seq(items) => {
                    if self.graph.form(id) == Some("import") && items.len() == 2 {
                        found.push(id);
                    }
                    pending.extend(items.iter().rev());
                }
                Kind::Map(entries) => pending.extend(entries.iter().rev().map(|(_, child)| *child)),
                Kind::Scalar { .. } => {}
            }
        }
        found
    }

    /// The root of the module imported by the `[import, SPEC]` node `import`, loading it if new.
    fn load(&mut self, import: NodeId, origin: &Origin) -> Result<NodeId, HelixError> {
        let Kind::Seq(items) = self.graph.kind(import) else { unreachable!("an import is a sequence") };
        let spec_node = items[1];
        let Kind::Scalar { value: Yaml::String(spec), .. } = self.graph.kind(spec_node) else {
            return Err(self.graph.error(spec_node, "type", "an import names its module with a literal string such as `lib/x.yaml` or `std:math`".into()));
        };
        let spec = spec.clone();
        let fail = |graph: &Graph, message: String| graph.error(spec_node, "import", message);
        let (key, label, source, next) = match spec.strip_prefix("std:") {
            Some(name) => match library::source(name) {
                Some(src) => (spec.clone(), spec.clone(), src.to_string(), Origin::Std),
                None => return Err(fail(self.graph, format!("unknown standard module `{name}`; available: {}", library::names().collect::<Vec<_>>().join(", ")))),
            },
            None => {
                self.modules.first_file_import.get_or_insert(import);
                let Origin::Dir(dir) = origin else { return Err(fail(self.graph, format!("standard modules can import only `std:` modules, not `{spec}`"))) };
                let path = dir.join(&spec);
                let source = std::fs::read_to_string(&path).map_err(|e| fail(self.graph, format!("cannot read `{}`: {e}", path.display())))?;
                let key = path.canonicalize().map_err(|e| fail(self.graph, format!("cannot resolve `{}`: {e}", path.display())))?.display().to_string();
                let next = Origin::Dir(path.parent().unwrap_or(Path::new(".")).to_path_buf());
                (key, path.display().to_string(), source, next)
            }
        };
        if let Some(at) = self.stack.iter().position(|(k, _)| *k == key) {
            let chain: Vec<&str> = self.stack[at..].iter().map(|(_, label)| label.as_str()).chain([label.as_str()]).collect();
            return Err(fail(self.graph, format!("circular import: {}", chain.join(" -> "))));
        }
        if let Some(&root) = self.loaded.get(&key) {
            return Ok(root);
        }
        let parsed = Graph::parse(&source).map_err(|e| HelixError { file: Some(label.clone()), ..e })?;
        let root = self.graph.graft(parsed);
        self.graph.set_source(root, label.clone());
        self.modules.roots.insert(root);
        self.stack.push((key.clone(), label));
        let scanned = self.scan(root, &next);
        self.stack.pop();
        scanned?;
        self.loaded.insert(key, root);
        Ok(root)
    }
}
