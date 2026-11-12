//! Bounded synchronous commands. Host policy and progress never enter machine state.
use std::collections::BTreeSet;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};
use super::failure::{Failure, fault, io_fault};

/// Caller-owned bounds, shared by every attempt of one command request.
#[derive(Clone, Debug)]
pub struct CommandPolicy {
    pub timeout_ms: u64,
    pub output_limit: usize,
    pub max_attempts: u32,
    pub retry_delay_ms: u64,
    /// Only normal nonzero exits explicitly classified by the caller may be retried.
    pub retry_exit_codes: BTreeSet<i32>,
}
impl Default for CommandPolicy {
    fn default() -> Self {
        Self { timeout_ms: 10_000, output_limit: 1024 * 1024, max_attempts: 1,
            retry_delay_ms: 0, retry_exit_codes: BTreeSet::new() }
    }
}
impl CommandPolicy {
    pub fn validate(&self) -> Result<(), Failure> {
        if !(1..=86_400_000).contains(&self.timeout_ms) {
            return Err(fault("policy", "command timeout must be 1..86400000 milliseconds"));
        }
        if !(1..=16 * 1024 * 1024).contains(&self.output_limit) {
            return Err(fault("policy", "command output limit must be 1..16777216 bytes per stream"));
        }
        if !(1..=32).contains(&self.max_attempts) || self.retry_delay_ms > 60_000 {
            return Err(fault("policy", "command attempts must be 1..32 and retry delay at most 60000 milliseconds"));
        }
        if self.retry_exit_codes.iter().any(|code| !(1..=255).contains(code)) {
            return Err(fault("policy", "transient command exit codes must be 1..255"));
        }
        Ok(())
    }
}

/// Metadata only: command arguments and output bytes are deliberately excluded.
#[derive(Clone, Debug)]
pub struct CommandProgress {
    pub effect_id: u64,
    pub attempt: u32,
    pub event: &'static str,
    pub elapsed_ms: u64,
    pub stdout_bytes: usize,
    pub stderr_bytes: usize,
    pub code: Option<i32>,
}
pub type ProgressCallback = Box<dyn FnMut(&CommandProgress) -> Result<(), Failure> + Send>;

pub(super) struct Control<'a> {
    pub policy: &'a CommandPolicy,
    pub cancellation: &'a AtomicBool,
    pub cancellation_signal: Option<&'static AtomicBool>,
    pub progress: &'a mut Option<ProgressCallback>,
    pub effect_id: u64,
}
struct Run<'a> {
    control: Control<'a>,
    started: Instant,
    heartbeat: Instant,
    attempt: u32,
    bytes: [usize; 2],
}
impl Run<'_> {
    fn event(&mut self, event: &'static str, code: Option<i32>) -> Result<(), Failure> {
        if let Some(callback) = self.control.progress.as_mut() {
            callback(&CommandProgress { effect_id: self.control.effect_id, attempt: self.attempt,
                event, elapsed_ms: self.started.elapsed().as_millis() as u64,
                stdout_bytes: self.bytes[0], stderr_bytes: self.bytes[1], code })?;
        }
        Ok(())
    }
    fn check(&mut self) -> Result<(), Failure> {
        if self.control.cancellation.load(Ordering::Relaxed)
            || self.control.cancellation_signal.is_some_and(|flag| flag.load(Ordering::Relaxed)) {
            return Err(fault("cancelled", "command cancelled; external changes may already have occurred"));
        }
        if self.started.elapsed() >= Duration::from_millis(self.control.policy.timeout_ms) {
            return Err(fault("timeout", "command exceeded its total timeout; external changes may already have occurred"));
        }
        if self.heartbeat.elapsed() >= Duration::from_millis(250) {
            self.event("heartbeat", None)?;
            self.heartbeat = Instant::now();
        }
        Ok(())
    }
    fn execute(&mut self, program: &str, args: &[&str]) -> Result<(i64, String, String), Failure> {
        loop {
            self.check()?;
            let result = attempt(program, args, self)?;
            self.check()?;
            let code = result.0 as i32;
            if self.attempt >= self.control.policy.max_attempts
                || !self.control.policy.retry_exit_codes.contains(&code) {
                self.event("finished", Some(code))?;
                return Ok(result);
            }
            self.event("retry", Some(code))?;
            let delay = Instant::now();
            while delay.elapsed() < Duration::from_millis(self.control.policy.retry_delay_ms) {
                self.check()?;
                std::thread::sleep(Duration::from_millis(5));
            }
            self.attempt += 1;
        }
    }
}
pub(super) fn capture(program: &str, args: &[&str], control: Control<'_>) -> Result<(i64, String, String), Failure> {
    control.policy.validate()?;
    let mut run = Run { control, started: Instant::now(), heartbeat: Instant::now(), attempt: 1, bytes: [0; 2] };
    let result = run.execute(program, args);
    if let Err((kind, _)) = &result {
        let event = match kind.as_str() { "timeout" => "timeout", "cancelled" => "cancelled", "resource" => "resource", _ => "failed" };
        // Preserve the original failure even when the reporting sink itself has failed.
        let _ = run.event(event, None);
    }
    result
}

#[cfg(unix)]
fn attempt(program: &str, args: &[&str], run: &mut Run<'_>) -> Result<(i64, String, String), Failure> {
    use std::os::unix::process::CommandExt;
    use std::process::{Command, Stdio};
    run.event("starting", None)?;
    run.check()?;
    let child = Command::new(program).args(args).stdin(Stdio::null()).stdout(Stdio::piped())
        .stderr(Stdio::piped()).process_group(0).spawn().map_err(io_fault)?;
    let mut child = ProcessGroup { child, reaped: false };
    let mut stdout = child.child.stdout.take().unwrap();
    let mut stderr = child.child.stderr.take().unwrap();
    let mut output = [Vec::new(), Vec::new()];
    let mut closed = [false; 2];
    let result = (|| {
        run.event("started", None)?;
        loop {
            run.check()?;
            let exited = child.exited()?;
            let before = run.bytes;
            // Once the leader exits, drain available bytes before deciding whether a
            // descendant is keeping its pipes open. Such an outcome is not retryable.
            loop {
                let mut read = false;
                read |= read_ready(&mut stdout, &mut output[0], &mut closed[0], &mut run.bytes[0], run.control.policy.output_limit)?;
                read |= read_ready(&mut stderr, &mut output[1], &mut closed[1], &mut run.bytes[1], run.control.policy.output_limit)?;
                if !exited || !read { break; }
                run.check()?;
            }
            if run.bytes != before { run.event("output", None)?; }
            if exited {
                let status = child.finish()?;
                if !closed.iter().all(|closed| *closed) {
                    return Err(fault("process", "command exited with descendant output pipes still open; outcome is uncertain"));
                }
                let stdout = String::from_utf8(std::mem::take(&mut output[0])).map_err(io_fault)?;
                let stderr = String::from_utf8(std::mem::take(&mut output[1])).map_err(io_fault)?;
                return Ok((status.code().map(i64::from).unwrap_or(-1), stdout, stderr));
            }
            if run.bytes == before { std::thread::sleep(Duration::from_millis(5)); }
        }
    })();
    if result.is_err() && !child.reaped {
        if let Err((_, cleanup)) = child.finish() {
            let (kind, message) = result.unwrap_err();
            return Err(fault("cleanup", &format!("{kind}: {message}; process-group cleanup failed: {cleanup}; command outcome is uncertain")));
        }
    }
    result
}

#[cfg(unix)]
fn read_ready<R: std::io::Read + std::os::fd::AsRawFd>(reader: &mut R, output: &mut Vec<u8>, closed: &mut bool, total: &mut usize, limit: usize) -> Result<bool, Failure> {
    if *closed { return Ok(false); }
    let mut poll = libc::pollfd { fd: reader.as_raw_fd(), events: libc::POLLIN, revents: 0 };
    // SAFETY: poll points to one initialized descriptor and never outlives it.
    let ready = unsafe { libc::poll(&mut poll, 1, 0) };
    if ready < 0 {
        let error = std::io::Error::last_os_error();
        if error.kind() == std::io::ErrorKind::Interrupted { return Ok(false); }
        return Err(io_fault(error));
    }
    if ready == 0 { return Ok(false); }
    let mut chunk = [0; 8192];
    let count = match reader.read(&mut chunk) {
        Ok(count) => count,
        Err(error) if error.kind() == std::io::ErrorKind::Interrupted => return Ok(false),
        Err(error) => return Err(io_fault(error)),
    };
    if count == 0 { *closed = true; return Ok(false); }
    *total += count;
    if *total > limit { return Err(fault("resource", "command exceeded its cumulative output limit")); }
    output.extend_from_slice(&chunk[..count]);
    Ok(true)
}

/// Retain the unreaped leader until group cleanup, so its process-group ID cannot
/// be reused between observing exit and sending the cleanup signal.
#[cfg(unix)]
struct ProcessGroup { child: std::process::Child, reaped: bool }
#[cfg(unix)]
impl ProcessGroup {
    fn exited(&self) -> Result<bool, Failure> {
        // SAFETY: zeroed siginfo_t is valid storage for waitid's output.
        let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
        // SAFETY: child PID identifies our unreaped child; info is writable.
        let result = unsafe { libc::waitid(libc::P_PID, self.child.id(), &mut info,
            libc::WEXITED | libc::WNOHANG | libc::WNOWAIT) };
        if result < 0 {
            let error = std::io::Error::last_os_error();
            if error.kind() == std::io::ErrorKind::Interrupted { return Ok(false); }
            return Err(io_fault(error));
        }
        // SAFETY: waitid initialized the SIGCHLD variant of siginfo_t.
        Ok(unsafe { info.si_pid() } != 0)
    }
    fn finish(&mut self) -> Result<std::process::ExitStatus, Failure> {
        // SAFETY: the unreaped child's PID reserves the process group identity;
        // a negative PID targets only the group created for this invocation.
        let killed = unsafe { libc::kill(-(self.child.id() as i32), libc::SIGKILL) };
        if killed < 0 {
            let error = std::io::Error::last_os_error();
            if error.raw_os_error() != Some(libc::ESRCH) { return Err(io_fault(error)); }
        }
        let status = self.child.wait().map_err(io_fault)?;
        self.reaped = true;
        Ok(status)
    }
}
#[cfg(unix)]
impl Drop for ProcessGroup {
    fn drop(&mut self) { if !self.reaped { let _ = self.finish(); } }
}
#[cfg(not(unix))]
fn attempt(_program: &str, _args: &[&str], _run: &mut Run<'_>) -> Result<(i64, String, String), Failure> {
    Err(fault("unsupported", "bounded command process groups require a Unix host"))
}
