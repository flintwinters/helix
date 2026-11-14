//! Binary transport with descriptor-relative, no-symlink traversal.
use std::{path::PathBuf, io::Read};
use yaml_rust2::Yaml;

#[cfg(unix)]
pub(super) fn read(descriptor: &Yaml, roots: &[PathBuf], limit: usize) -> Result<Vec<u8>, String> {
    use std::{os::{fd::{AsRawFd, FromRawFd}}, ffi::CString};
    let root = descriptor["directory"].as_str().ok_or("static directory must be text")?;
    let path = descriptor["path"].as_str().ok_or("static path must be text")?;
    if path.contains(['%', '\\', '\0']) { return Err("encoded or platform path escapes are forbidden".into()); }
    let path = path.strip_prefix('/').unwrap_or(path);
    let parts = path.split('/').collect::<Vec<_>>();
    if parts.iter().any(|part| matches!(*part,"" | "." | "..")) { return Err("invalid static path".into()); }
    let root = super::super::host::authorized(root,roots,false).map_err(|(_,why)| why)?;
    let mut file = std::fs::File::open("/").map_err(|e|e.to_string())?;
    for component in root.components() {
        let std::path::Component::Normal(part) = component else { continue };
        use std::os::unix::ffi::OsStrExt;
        let part = CString::new(part.as_bytes()).map_err(|e| e.to_string())?;
        let fd = unsafe { libc::openat(file.as_raw_fd(),part.as_ptr(),libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC) };
        if fd < 0 { return Err(std::io::Error::last_os_error().to_string()); }
        file = unsafe { std::fs::File::from_raw_fd(fd) };
    }
    for (index,part) in parts.iter().enumerate() {
        let part = CString::new(*part).map_err(|e|e.to_string())?;
        let flags = libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK
            | if index + 1 == parts.len() { 0 } else { libc::O_DIRECTORY };
        // Each descriptor owns its directory inode; replacing a path or symlink
        // cannot redirect subsequent traversal outside the authorized directory.
        let fd = unsafe { libc::openat(file.as_raw_fd(),part.as_ptr(),flags) };
        if fd < 0 { return Err(std::io::Error::last_os_error().to_string()); }
        file = unsafe { std::fs::File::from_raw_fd(fd) };
    }
    let metadata = file.metadata().map_err(|e|e.to_string())?;
    if !metadata.is_file() || metadata.len() > limit as u64 { return Err("static transfer requires a bounded regular file".into()); }
    let mut bytes = Vec::new();
    file.take(limit as u64 + 1).read_to_end(&mut bytes).map_err(|e|e.to_string())?;
    if bytes.len() > limit { return Err("static file grew beyond policy".into()); }
    Ok(bytes)
}
#[cfg(not(unix))]
pub(super) fn read(_: &Yaml, _: &[PathBuf], _: usize) -> Result<Vec<u8>, String> {
    Err("static directory transport requires Unix descriptor-relative paths".into())
}
