//! `dir_list`: bounded, sorted directory listings that never follow symlinks.
use std::path::Path;
use yaml_rust2::Yaml;
use super::{Failure, Reply, fault, io_fault, object, text};

const ENTRIES: usize = 10_000;

/// Payload: a path string, or `{path, recursive?}`. Returns the entries under the path
/// sorted by path relative to it, each `{path, kind}` with kind `file`, `dir`, `symlink` or `other`.
pub(super) fn parse(payload: &Yaml) -> Result<(&str, bool), Failure> {
    if let Yaml::String(path) = payload { return Ok((path, false)); }
    let recursive = match &payload["recursive"] {
        Yaml::BadValue | Yaml::Null => false,
        Yaml::Boolean(flag) => *flag,
        _ => return Err(fault("type", "dir_list recursive must be a boolean")),
    };
    Ok((text(&payload["path"])?, recursive))
}

/// Symlinks are reported, never traversed, so a listing cannot leave the authorized root.
pub(super) fn list(root: &Path, recursive: bool) -> Reply {
    let mut found: Vec<(String, &'static str)> = Vec::new();
    let mut pending = vec![(root.to_path_buf(), String::new())];
    while let Some((directory, prefix)) = pending.pop() {
        for entry in std::fs::read_dir(&directory).map_err(io_fault)? {
            let entry = entry.map_err(io_fault)?;
            let name = entry.file_name().into_string()
                .map_err(|name| fault("conversion", &format!("directory entry {name:?} is not UTF-8")))?;
            let kind = match entry.file_type().map_err(io_fault)? {
                kind if kind.is_symlink() => "symlink",
                kind if kind.is_dir() => "dir",
                kind if kind.is_file() => "file",
                _ => "other",
            };
            if found.len() >= ENTRIES {
                return Err(fault("resource", &format!("directory listing exceeds {ENTRIES} entries; list a narrower directory")));
            }
            let relative = format!("{prefix}{name}");
            if recursive && kind == "dir" { pending.push((entry.path(), format!("{relative}/"))); }
            found.push((relative, kind));
        }
    }
    found.sort();
    Ok(Yaml::Array(found.into_iter().map(|(path, kind)|
        object([("path", Yaml::String(path)), ("kind", Yaml::String(kind.into()))])).collect()))
}
