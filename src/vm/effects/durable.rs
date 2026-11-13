//! Caller-owned durable effect boundary. An in-flight record is deliberately
//! uncertain after process death; only an explicit observed outcome resolves it.
use super::EffectHost;
use crate::{EffectRequest, Status, Vm};
use serde_json::Value as J;
use std::fs::{File, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use yaml_rust2::Yaml;
const MAX_BYTES: u64 = 40 * 1024 * 1024;

pub struct Checkpoint {
    path: PathBuf,
    _lock: File,
    pub(crate) exit_code: u8,
    pub(crate) random_seed: Option<u64>,
}
impl Checkpoint {
    fn lock(path: &Path) -> Result<Self, String> {
        let parent = path
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        let name = path.file_name().ok_or("checkpoint requires a filename")?;
        let lock = parent.join(format!("{}.lock", name.to_string_lossy()));
        let mut options = OpenOptions::new();
        options.read(true).write(true).create(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600).custom_flags(libc::O_NOFOLLOW);
        }
        let file = options
            .open(&lock)
            .map_err(|e| format!("checkpoint lock: {e}"))?;
        #[cfg(unix)]
        {
            use std::os::fd::AsRawFd;
            if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
                return Err("checkpoint is owned by another controller".into());
            }
        }
        #[cfg(not(unix))]
        {
            return Err("durable effect checkpoints currently require Unix file locks".into());
        }
        Ok(Self {
            path: path.to_owned(),
            _lock: file,
            exit_code: 0,
            random_seed: None,
        })
    }
    /// Start a new checkpoint. Existing execution history is never overwritten.
    pub fn create(path: &Path) -> Result<Self, String> {
        let checkpoint = Self::lock(path)?;
        if path.symlink_metadata().is_ok() {
            return Err("checkpoint already exists; resume it or choose a new file".into());
        }
        Ok(checkpoint)
    }
    fn load(&mut self) -> Result<(Vm, String), String> {
        let mut text = String::new();
        File::open(&self.path)
            .map_err(|e| e.to_string())?
            .take(MAX_BYTES + 1)
            .read_to_string(&mut text)
            .map_err(|e| e.to_string())?;
        if text.len() as u64 > MAX_BYTES {
            return Err("effect checkpoint exceeds byte budget".into());
        }
        let doc: J =
            serde_json::from_str(&text).map_err(|e| format!("invalid effect checkpoint: {e}"))?;
        let map = doc.as_object().ok_or("expected effect checkpoint map")?;
        if map.len() != 6 || doc["format"] != "helix-effects" || doc["version"] != 1 {
            return Err("unsupported effect checkpoint format/version".into());
        }
        if doc["authority"] != "caller-required"
            || doc["host"].as_object().is_none_or(|h| h.len() != 2)
        {
            return Err("invalid checkpoint host metadata".into());
        }
        let phase = doc["phase"]
            .as_str()
            .filter(|s| matches!(*s, "ready" | "in_flight"))
            .ok_or("unknown effect checkpoint phase")?
            .to_owned();
        self.exit_code = doc["host"]["exit_code"]
            .as_u64()
            .filter(|n| *n <= 255)
            .ok_or("invalid saved exit code")? as u8;
        self.random_seed = if doc["host"]["random_seed"].is_null() {
            None
        } else {
            Some(
                doc["host"]["random_seed"]
                    .as_u64()
                    .ok_or("invalid saved random state")?,
            )
        };
        let vm = Vm::restore(&doc["snapshot"].to_string())?;
        if phase == "in_flight" && vm.pending_effect().is_none() {
            return Err("in-flight checkpoint has no pending request".into());
        }
        Ok((vm, phase))
    }
    /// Resume only a checkpoint whose pending request is known not to have started.
    pub fn resume(path: &Path) -> Result<(Self, Vm), String> {
        let mut checkpoint = Self::lock(path)?;
        let (vm, phase) = checkpoint.load()?;
        if phase == "in_flight" {
            return Err("uncertain effect outcome: observe the target and reconcile the exact pending request before resuming".into());
        }
        Ok((checkpoint, vm.resume_signal()))
    }
    pub(crate) fn save(&self, vm: &Vm, phase: &str, host: &EffectHost) -> Result<(), String> {
        let snapshot: J = serde_json::from_str(&vm.snapshot()?).map_err(|e| e.to_string())?;
        let text=serde_json::to_vec_pretty(&serde_json::json!({"format":"helix-effects","version":1,"phase":phase,
            "host":{"exit_code":host.exit_code,"random_seed":host.random_seed},"snapshot":snapshot,"authority":"caller-required"})).map_err(|e|e.to_string())?;
        if text.len() as u64 > MAX_BYTES {
            return Err("effect checkpoint exceeds byte budget".into());
        }
        let parent = self
            .path
            .parent()
            .filter(|p| !p.as_os_str().is_empty())
            .unwrap_or(Path::new("."));
        let name = self.path.file_name().unwrap().to_string_lossy();
        let stamp = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_err(|e| e.to_string())?
            .as_nanos();
        let temporary = parent.join(format!(".{name}.{}.{stamp}.tmp", std::process::id()));
        let result = (|| -> Result<(), String> {
            let mut options = OpenOptions::new();
            options.write(true).create_new(true);
            #[cfg(unix)]
            {
                use std::os::unix::fs::OpenOptionsExt;
                options.mode(0o600);
            }
            let mut file = options.open(&temporary).map_err(|e| e.to_string())?;
            file.write_all(&text)
                .and_then(|_| file.sync_all())
                .map_err(|e| e.to_string())?;
            std::fs::rename(&temporary, &self.path).map_err(|e| e.to_string())?;
            File::open(parent)
                .and_then(|f| f.sync_all())
                .map_err(|e| e.to_string())?;
            Ok(())
        })();
        if result.is_err() {
            let _ = std::fs::remove_file(&temporary);
        }
        result
    }
    /// The caller supplies an independently observed outcome for this exact request.
    /// This records the outcome; it never executes the uncertain effect itself.
    pub fn reconcile(
        path: &Path,
        expected: &EffectRequest,
        outcome: Result<Yaml, (String, String)>,
    ) -> Result<(), String> {
        let mut checkpoint = Self::lock(path)?;
        let (vm, phase) = checkpoint.load()?;
        if phase != "in_flight" || vm.pending_effect() != Some(expected) {
            return Err("reconciliation must match the exact uncertain request".into());
        }
        if let Ok(observed) = &outcome {
            if expected.operation == "exit" {
                checkpoint.exit_code = expected
                    .payload
                    .as_i64()
                    .filter(|n| (0..=255).contains(n))
                    .ok_or("invalid reconciled exit code")?
                    as u8;
            }
            if expected.operation == "random" {
                if let Some(seed) = checkpoint.random_seed.as_mut() {
                    let next = super::next_random(seed);
                    if observed != &Yaml::Integer(next as i64) {
                        return Err(
                            "observed random outcome disagrees with saved deterministic state"
                                .into(),
                        );
                    }
                }
            }
        }
        let resolved = vm.resolve_effect(outcome);
        let host = EffectHost {
            exit_code: checkpoint.exit_code,
            random_seed: checkpoint.random_seed,
            ..Default::default()
        };
        checkpoint.save(&resolved, "ready", &host)
    }
}
impl EffectHost {
    /// Flush the pending request before dispatch and its completed outcome afterward.
    /// No saved field carries host authority; this host supplies fresh caller grants.
    pub fn run_checkpointed(&mut self, vm: &mut Vm, checkpoint: &Checkpoint) -> Result<(), String> {
        self.exit_code = checkpoint.exit_code;
        if checkpoint.random_seed.is_some() {
            self.random_seed = checkpoint.random_seed;
        }
        loop {
            vm.run();
            checkpoint.save(vm, "ready", self)?;
            if vm.status() != Status::Signaled
                || vm.is_interrupted()
                || vm.pending_effect().is_none()
            {
                return Ok(());
            }
            checkpoint.save(vm, "in_flight", self)?;
            let handled = self.resolve_pending(vm);
            checkpoint.save(vm, "ready", self)?;
            if !handled {
                return Ok(());
            }
        }
    }
}
