//! Caller-owned host capabilities. Source and saved state cannot grant authority.
use std::io::{Read, Write};
use std::time::{SystemTime, UNIX_EPOCH};
use std::sync::atomic::Ordering;
#[path = "effects/failure.rs"]
mod failure;
#[path = "effects/http.rs"]
pub(crate) mod http;
use failure::{Failure, Reply, fault, io_fault};
#[path = "effects/host.rs"]
mod host;
use host::{require, object, authorized};
#[path = "effects/sqlite.rs"]
mod sqlite;
#[path = "effects/directory.rs"]
mod directory;
#[path = "effects/command.rs"]
mod command;
#[path = "effects/ssh.rs"]
mod ssh;
#[path = "effects/locks.rs"]
pub mod locks;
#[path = "effects/secrets.rs"]
mod secrets;
#[path = "effects/durable.rs"]
pub mod durable;
pub use host::{EffectHost, ServerPolicy, ServerProgress};
pub use ssh::SshTarget;
pub use command::{CommandPolicy, CommandProgress, ProgressCallback};
use yaml_rust2::Yaml;
use crate::model::{Status, Vm};

const LIMIT: usize = 1024 * 1024;

impl EffectHost {
    /// Stop persistent SSH connections this host may have started; call once when the session ends.
    pub fn close_ssh_connections(&self) {
        if let Some(dir) = &self.ssh_control_dir { ssh::close_all(&self.ssh_targets, dir); }
    }
    pub fn run(&mut self, vm: &mut Vm) {
        loop {
            vm.run();
            if !self.resolve_pending(vm) { break; }
        }
    }
    /// Shared dispatch boundary for ephemeral and journaled controllers.
    fn resolve_pending(&mut self, vm: &mut Vm) -> bool {
        if vm.status() != Status::Signaled || vm.is_interrupted() { return false; }
        // Promise enforcement must happen before dispatch: an external action
        // cannot be rolled back when its reply exceeds a serialization budget.
        if vm.pending_effect().is_some_and(|request| !vm.can_dispatch_effect(&request.operation)) {
            *vm = vm.resolve_effect(Err(("promise".into(),
                "host dispatch is unavailable for promised machines".into())));
            return false;
        }
        let Some(request) = vm.pending_effect() else { return false; };
        if self.cancellation.load(Ordering::Relaxed)
            || self.cancellation_signal.is_some_and(|flag| flag.load(Ordering::Relaxed)) {
            *vm = vm.signal(object([("kind", Yaml::String("cancelled".into()))]));
            return false;
        }
        let reply = if request.operation == "http_serve" {
            http::serve(self, vm)
        } else {
            self.handle_secret(request.id, &request.operation, &request.payload)
        };
        *vm = vm.resolve_effect(reply);
        true
    }

    fn handle_plain(&mut self, effect_id: u64, operation: &str, payload: &Yaml) -> Reply {
        match operation {
            "args" => Ok(Yaml::Array(self.args.iter().cloned().map(Yaml::String).collect())),
            "exit" => {
                let code = payload.as_i64().filter(|n| (0..=255).contains(n))
                    .ok_or_else(|| fault("type", "exit requires an integer from 0 to 255"))?;
                self.exit_code = code as u8;
                Ok(Yaml::Null)
            }
            "file_read" => {
                let path = authorized(text(payload)?, &self.read_roots, false)?;
                read_text(std::fs::File::open(path).map_err(io_fault)?).map(Yaml::String)
            }
            "dir_list" => {
                let (path, recursive) = directory::parse(payload)?;
                directory::list(&authorized(path, &self.read_roots, false)?, recursive)
            }
            "file_write" => {
                let path = authorized(text(&payload["path"])?, &self.write_roots, true)?;
                let content = text(&payload["text"])?;
                if content.len() > LIMIT { return Err(fault("resource", "file content exceeds 1 MiB")); }
                std::fs::write(path, content).map_err(io_fault)?;
                Ok(Yaml::Null)
            }
            "sqlite" => {
                let write = match &payload["write"] {
                    Yaml::BadValue | Yaml::Null => false,
                    Yaml::Boolean(flag) => *flag,
                    _ => return Err(fault("type", "sqlite write must be a boolean")),
                };
                let request = sqlite::Request::parse(payload)?;
                // Database files follow the file grants: reading needs a read root, any write needs a write root.
                let path = text(&payload["path"])?;
                let path = if write { authorized(path, &self.write_roots, true)? } else { authorized(path, &self.read_roots, false)? };
                sqlite::run(&path, write, &request)
            }
            "env" => {
                let name = text(payload)?;
                require(self.env_names.contains(name), "environment variable")?;
                match std::env::var(name) {
                    Ok(value) => Ok(Yaml::String(value)),
                    Err(std::env::VarError::NotPresent) => Ok(Yaml::Null),
                    Err(error) => Err(io_fault(error)),
                }
            }
            "stdin" => { require(self.allow_stdin, "stdin")?; read_text(std::io::stdin()).map(Yaml::String) }
            "stdout" => {
                require(self.allow_stdout, "stdout")?;
                let value = text(payload)?;
                if value.len() > LIMIT { return Err(fault("resource", "stdout content exceeds 1 MiB")); }
                let mut output = std::io::stdout().lock();
                output.write_all(value.as_bytes()).map_err(io_fault)?;
                output.flush().map_err(io_fault)?;
                Ok(Yaml::Null)
            }
            "command" => {
                let program = text(&payload["program"])?;
                require(self.commands.contains(program), "command executable")?;
                let args = payload["args"].as_vec().ok_or_else(|| fault("type", "command args must be a list of strings"))?;
                let args = args.iter().map(text).collect::<Result<Vec<_>, _>>()?;
                let (code, stdout, stderr) = command::capture(program, &args, command::Control {
                    policy: &self.command_policy, cancellation: &self.cancellation,
                    cancellation_signal: self.cancellation_signal, progress: &mut self.command_progress, effect_id,
                    cwd: None, stdin: command_input(&payload["stdin"])?,
                })?;
                Ok(object([("code", Yaml::Integer(code)), ("stdout", Yaml::String(stdout)), ("stderr", Yaml::String(stderr))]))
            }
            "ssh_command" | "ssh_file_read" | "ssh_file_write" => {
                let alias = text(&payload["host"])?;
                let target = self.ssh_targets.get(alias).ok_or_else(|| fault("capability", "SSH target capability not granted"))?;
                ssh::run(operation, target, ssh::Mux { dir: self.ssh_control_dir.as_deref() }, payload, command::Control {
                    policy: &self.command_policy, cancellation: &self.cancellation,
                    cancellation_signal: self.cancellation_signal, progress: &mut self.command_progress, effect_id,
                    cwd: None, stdin: None,
                })
            }
            "http" => {
                let url = text(payload)?;
                let origin = origin(url)?;
                require(self.http_origins.contains(origin), "HTTP origin")?;
                // No redirects, proxies, curl config, cookies or URL globbing. Grant is exact origin.
                let (code, mut output, error) = command::capture("curl", &["-q", "--silent", "--show-error", "--globoff", "--noproxy", "*", "--proto", "=http,https", "--max-time", "8", "--max-filesize", "1048576", "--write-out", "\n%{http_code}", "--url", url], command::Control { policy: &CommandPolicy::default(),
                    cancellation: &self.cancellation, cancellation_signal: self.cancellation_signal,
                    progress: &mut None, effect_id, cwd: None, stdin: None })?;
                if code != 0 { return Err(fault("http", &error)); }
                let (body, status) = output.rsplit_once('\n').ok_or_else(|| fault("http", "missing HTTP status"))?;
                let status = status.parse::<i64>().map_err(io_fault)?;
                output.truncate(body.len());
                Ok(object([("status", Yaml::Integer(status)), ("body", Yaml::String(output))]))
            }
            "time" => {
                require(self.allow_time, "time")?;
                let now = match self.fixed_time { Some(now) => now, None => SystemTime::now().duration_since(UNIX_EPOCH).map_err(io_fault)?.as_millis().try_into().map_err(io_fault)? };
                Ok(Yaml::Integer(now))
            }
            "random" => {
                require(self.allow_random, "random")?;
                let value = if let Some(seed) = self.random_seed.as_mut() {
                    next_random(seed)
                } else {
                    let mut bytes = [0; 8];
                    std::fs::File::open("/dev/urandom").and_then(|mut f| f.read_exact(&mut bytes)).map_err(io_fault)?;
                    u64::from_le_bytes(bytes) >> 1
                };
                Ok(Yaml::Integer(value as i64))
            }
            _ => Err(fault("effect", &format!("unknown effect operation `{operation}`"))),
        }
    }
}
fn text(value: &Yaml) -> Result<&str, Failure> { value.as_str().ok_or_else(|| fault("type", "effect payload must be a string")) }
fn read_text(reader: impl Read) -> Result<String, Failure> {
    let mut bytes = Vec::new();
    reader.take((LIMIT + 1) as u64).read_to_end(&mut bytes).map_err(io_fault)?;
    if bytes.len() > LIMIT { return Err(fault("resource", "effect input exceeds 1 MiB")); }
    String::from_utf8(bytes).map_err(io_fault)
}
fn origin(url: &str) -> Result<&str, Failure> {
    let rest = url.strip_prefix("http://").or_else(|| url.strip_prefix("https://")).ok_or_else(|| fault("type", "HTTP URL must use http or https"))?;
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    if authority.is_empty() || authority.contains(['@', '\\']) || url.chars().any(char::is_whitespace) { return Err(fault("type", "invalid HTTP authority")); }
    Ok(&url[..url.len() - rest.len() + authority.len()])
}

fn next_random(seed: &mut u64) -> u64 {
    *seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
    *seed >> 1
}

/// Local and SSH commands share the same bounded optional UTF-8 stdin contract.
fn command_input(value: &Yaml) -> Result<Option<&[u8]>, Failure> {
    match value {
        Yaml::BadValue | Yaml::Null => Ok(None),
        Yaml::String(text) if text.len() <= LIMIT => Ok(Some(text.as_bytes())),
        Yaml::String(_) => Err(fault("resource", "command stdin exceeds 1 MiB")),
        _ => Err(fault("type", "command stdin must be a string or granted secret handle")),
    }
}
