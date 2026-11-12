//! Caller-owned host capabilities. Source and saved state cannot grant authority.
use std::collections::BTreeSet;
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use yaml_rust2::Yaml;
use crate::model::{Status, Vm};

type Failure = (String, String);
type Reply = Result<Yaml, Failure>;
const LIMIT: usize = 1024 * 1024;

/// A host is deliberately separate from immutable machine values. Grants never come from source.
#[derive(Default)]
pub struct EffectHost {
    pub read_roots: Vec<PathBuf>,
    pub write_roots: Vec<PathBuf>,
    pub env_names: BTreeSet<String>,
    pub commands: BTreeSet<String>,
    pub http_origins: BTreeSet<String>,
    pub allow_stdin: bool,
    pub allow_stdout: bool,
    pub allow_time: bool,
    pub allow_random: bool,
    pub args: Vec<String>,
    pub fixed_time: Option<i64>,
    pub random_seed: Option<u64>,
    pub exit_code: u8,
}

impl EffectHost {
    pub fn run(&mut self, vm: &mut Vm) {
        loop {
            vm.run();
            if vm.status() != Status::Signaled { break; }
            if vm.is_interrupted() { break; }
            let Some(request) = vm.pending_effect() else { break };
            let reply = self.handle(&request.operation, &request.payload);
            *vm = vm.resolve_effect(reply);
        }
    }
    fn handle(&mut self, operation: &str, payload: &Yaml) -> Reply {
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
            "file_write" => {
                let path = authorized(text(&payload["path"])?, &self.write_roots, true)?;
                let content = text(&payload["text"])?;
                if content.len() > LIMIT { return Err(fault("resource", "file content exceeds 1 MiB")); }
                std::fs::write(path, content).map_err(io_fault)?;
                Ok(Yaml::Null)
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
                let (code, stdout, stderr) = capture(program, &args)?;
                Ok(object([("code", Yaml::Integer(code)), ("stdout", Yaml::String(stdout)), ("stderr", Yaml::String(stderr))]))
            }
            "http" => {
                let url = text(payload)?;
                let origin = origin(url)?;
                require(self.http_origins.contains(origin), "HTTP origin")?;
                // No redirects, proxies, curl config, cookies or URL globbing. Grant is exact origin.
                let (code, mut output, error) = capture("curl", &["-q", "--silent", "--show-error", "--globoff", "--noproxy", "*", "--proto", "=http,https", "--max-time", "8", "--max-filesize", "1048576", "--write-out", "\n%{http_code}", "--url", url])?;
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
                    *seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
                    *seed >> 1
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
fn fault(kind: &str, message: &str) -> Failure { (kind.into(), message.into()) }
fn io_fault(error: impl std::fmt::Display) -> Failure { fault("io", &error.to_string()) }
fn require(granted: bool, name: &str) -> Result<(), Failure> {
    if granted { Ok(()) } else { Err(fault("capability", &format!("{name} capability not granted"))) }
}
fn text(value: &Yaml) -> Result<&str, Failure> { value.as_str().ok_or_else(|| fault("type", "effect payload must be a string")) }
fn object<const N: usize>(fields: [(&str, Yaml); N]) -> Yaml {
    Yaml::Hash(fields.into_iter().map(|(key, value)| (Yaml::String(key.into()), value)).collect())
}
fn read_text(reader: impl Read) -> Result<String, Failure> {
    let mut bytes = Vec::new();
    reader.take((LIMIT + 1) as u64).read_to_end(&mut bytes).map_err(io_fault)?;
    if bytes.len() > LIMIT { return Err(fault("resource", "effect input exceeds 1 MiB")); }
    String::from_utf8(bytes).map_err(io_fault)
}
fn authorized(path: &str, roots: &[PathBuf], write: bool) -> Result<PathBuf, Failure> {
    let path = Path::new(path);
    let resolved = if write && matches!(std::fs::symlink_metadata(path), Err(ref error) if error.kind() == std::io::ErrorKind::NotFound) {
        let parent = path.parent().filter(|p| !p.as_os_str().is_empty()).unwrap_or(Path::new("."));
        parent.canonicalize().map_err(io_fault)?.join(path.file_name().ok_or_else(|| fault("io", "missing filename"))?)
    } else { path.canonicalize().map_err(io_fault)? };
    require(roots.iter().any(|root| root.canonicalize().is_ok_and(|root| resolved.starts_with(root))), "file path")?;
    Ok(resolved)
}
fn origin(url: &str) -> Result<&str, Failure> {
    let rest = url.strip_prefix("http://").or_else(|| url.strip_prefix("https://")).ok_or_else(|| fault("type", "HTTP URL must use http or https"))?;
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    if authority.is_empty() || authority.contains(['@', '\\']) || url.chars().any(char::is_whitespace) { return Err(fault("type", "invalid HTTP authority")); }
    Ok(&url[..url.len() - rest.len() + authority.len()])
}
fn capture(program: &str, args: &[&str]) -> Result<(i64, String, String), Failure> {
    let mut child = Command::new(program).args(args).stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped()).spawn().map_err(io_fault)?;
    let stdout = child.stdout.take().unwrap();
    let stderr = child.stderr.take().unwrap();
    let (out_tx, out) = std::sync::mpsc::channel();
    let (err_tx, err) = std::sync::mpsc::channel();
    std::thread::spawn(move || { let _ = out_tx.send(read_text(stdout)); });
    std::thread::spawn(move || { let _ = err_tx.send(read_text(stderr)); });
    let start = Instant::now();
    let status = loop {
        if let Some(status) = child.try_wait().map_err(io_fault)? { break status; }
        if start.elapsed() > Duration::from_secs(10) {
            let _ = child.kill(); let _ = child.wait();
            return Err(fault("timeout", "command exceeded 10 seconds"));
        }
        std::thread::sleep(Duration::from_millis(5));
    };
    let output = out.recv_timeout(Duration::from_secs(10).saturating_sub(start.elapsed())).map_err(|_| fault("timeout", "command output pipe remained open"))??;
    let error = err.recv_timeout(Duration::from_secs(10).saturating_sub(start.elapsed())).map_err(|_| fault("timeout", "command error pipe remained open"))??;
    Ok((status.code().map(i64::from).unwrap_or(-1), output, error))
}
