//! Caller-selected coordination domains. Never infer authority from lock contents.
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::Path;

pub(super) fn exclusive(path: &Path) -> Result<File, String> {
    let mut options = OpenOptions::new();
    options.read(true).write(true).create(true);
    #[cfg(unix)] {
        use std::os::{fd::AsRawFd, unix::fs::OpenOptionsExt};
        options.mode(0o600).custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
        let file = options.open(path).map_err(|e| format!("controller lock: {e}"))?;
        if !file.metadata().map_err(|e| e.to_string())?.is_file() {
            return Err("controller lock must be a regular file".into());
        }
        if unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            return Err("lock is owned by another controller".into());
        }
        Ok(file)
    }
    #[cfg(not(unix))] {
        Err("controller coordination currently requires Unix file locks".into())
    }
}

const AVAILABLE: &[u8] = b"helix-target-v1\n0\n";
const HELD: &[u8] = b"helix-target-v1\n1\n";

/// A persistent uncertainty marker complements the live controller's flock.
/// Cooperating controllers must select the same file for the same target.
pub struct TargetGuard(File);
impl TargetGuard {
    fn open(path: &Path) -> Result<(Self, Vec<u8>), String> {
        let mut file = exclusive(path)?;
        let mut bytes = Vec::new();
        (&mut file).take(64).read_to_end(&mut bytes).map_err(|e| e.to_string())?;
        if !bytes.is_empty() && bytes != AVAILABLE && bytes != HELD {
            return Err("invalid target lock record; inspect and repair it independently".into());
        }
        Ok((Self(file), bytes))
    }
    pub fn acquire(path: &Path) -> Result<Self, String> {
        let (mut guard, bytes) = Self::open(path)?;
        if bytes == HELD {
            return Err("target has an unreleased operation; establish quiescence and reconcile its journal before target-release".into());
        }
        guard.write(HELD)?;
        // Persist the directory entry before any external operation may start.
        let parent = path.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or(Path::new("."));
        File::open(parent).and_then(|f| f.sync_all()).map_err(|e| e.to_string())?;
        Ok(guard)
    }
    fn write(&mut self, bytes: &[u8]) -> Result<(), String> {
        self.0.seek(SeekFrom::Start(0)).and_then(|_| self.0.write_all(bytes))
            .and_then(|_| self.0.sync_all()).map_err(|e| e.to_string())
    }
    /// Only call after observing that the managed operation has stopped.
    pub fn release(mut self) -> Result<(), String> { self.write(AVAILABLE) }
    /// Explicit operator acknowledgement; never steals a live controller's lock.
    pub fn acknowledge(path: &Path) -> Result<(), String> {
        let (guard, _) = Self::open(path)?;
        guard.release()
    }
}
