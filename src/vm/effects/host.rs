//! Caller-owned grants, ceilings and shared file authorization, separate from dispatch.
use std::{collections::{BTreeMap,BTreeSet}, path::{Path,PathBuf}, sync::{Arc,atomic::AtomicBool}};
use yaml_rust2::Yaml;
use super::{command::{CommandPolicy,ProgressCallback}, ssh::SshTarget, failure::{Reply,Failure,fault,io_fault}};

#[derive(Clone, Debug)]
pub struct ServerPolicy {
    pub connections: usize,
    pub handlers: usize,
    pub actors: usize,
    pub mailbox: usize,
    pub headers: usize,
    pub body: usize,
    pub static_file: usize,
    pub deadline_ms: u64,
}
impl Default for ServerPolicy {
    fn default() -> Self {
        Self { connections: 32, handlers: 8, actors: 8, mailbox: 64, headers: 16 * 1024,
            body: 256 * 1024, static_file: 1024 * 1024, deadline_ms: 10_000 }
    }
}
impl ServerPolicy {
    pub fn validate(&self) -> Result<(), String> {
        if self.connections == 0 || self.connections > 4096 || self.handlers == 0 || self.handlers > 256
            || self.actors > 256 || self.mailbox == 0 || self.mailbox > 4096 || self.headers < 8192
            || self.headers > 1024 * 1024 || self.body == 0 || self.body > 1024 * 1024
            || self.static_file == 0 || self.static_file > 16 * 1024 * 1024
            || self.deadline_ms == 0 || self.deadline_ms > 86_400_000 {
            Err("invalid HTTP server policy bounds".into())
        } else { Ok(()) }
    }
}
pub type ServerProgress = Box<dyn FnMut(&serde_json::Value) -> Reply + Send>;

/// A host is deliberately separate from immutable machine values. Grants never come from source.
#[derive(Default)]
pub struct EffectHost {
    pub read_roots: Vec<PathBuf>,
    pub write_roots: Vec<PathBuf>,
    pub env_names: BTreeSet<String>,
    pub commands: BTreeSet<String>,
    /// Caller-owned opaque secret references; credential bytes never enter the VM.
    pub secret_files: BTreeMap<String, PathBuf>,
    pub command_policy: CommandPolicy,
    pub cancellation: Arc<AtomicBool>,
    pub cancellation_signal: Option<&'static AtomicBool>,
    pub command_progress: Option<ProgressCallback>,
    pub listen_addresses: BTreeSet<std::net::SocketAddr>,
    pub server_policy: ServerPolicy,
    pub server_progress: Option<ServerProgress>,
    pub http_origins: BTreeSet<String>,
    pub ssh_targets: BTreeMap<String, SshTarget>,
    /// Private directory for connection-reuse sockets; `None` disables reuse.
    pub ssh_control_dir: Option<PathBuf>,
    pub allow_stdin: bool,
    pub allow_stdout: bool,
    pub allow_time: bool,
    pub allow_random: bool,
    pub args: Vec<String>,
    pub fixed_time: Option<i64>,
    pub random_seed: Option<u64>,
    pub exit_code: u8,
}

pub(super) fn require(granted: bool, name: &str) -> Result<(), Failure> {
    if granted { Ok(()) } else { Err(fault("capability", &format!("{name} capability not granted"))) }
}
pub(super) fn object<const N: usize>(fields: [(&str, Yaml); N]) -> Yaml {
    Yaml::Hash(fields.into_iter().map(|(key, value)| (Yaml::String(key.into()), value)).collect())
}
pub(super) fn authorized(path: &str, roots: &[PathBuf], write: bool) -> Result<PathBuf, Failure> {
    let path = Path::new(path);
    let resolved = if write && matches!(std::fs::symlink_metadata(path), Err(ref error) if error.kind() == std::io::ErrorKind::NotFound) {
        let parent = path.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or(Path::new("."));
        parent.canonicalize().map_err(io_fault)?.join(path.file_name().ok_or_else(|| fault("io", "missing filename"))?)
    } else { path.canonicalize().map_err(io_fault)? };
    require(roots.iter().any(|root| root.canonicalize().is_ok_and(|root| resolved.starts_with(root))), "file path")?;
    Ok(resolved)
}
