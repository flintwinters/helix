//! Remote commands over the system OpenSSH client. Grants name exact targets; source cannot
//! choose a destination, key, host-key policy or escalation identity.
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use yaml_rust2::Yaml;
use super::command::{self, Control};
use super::failure::{Failure, fault};

/// One caller-authorized remote target, addressed from source only by its alias.
#[derive(Clone, Debug, Default, Hash)]
pub struct SshTarget {
    pub host: String,
    pub user: Option<String>,
    pub port: Option<u16>,
    /// Mandatory: host keys are never accepted on first use.
    pub known_hosts: PathBuf,
    pub identity: Option<PathBuf>,
    pub jump: Option<String>,
    /// Execution identities a request may escalate to with non-interactive sudo.
    pub become_users: BTreeSet<String>,
}

fn plain_name(name: &str) -> bool {
    !name.is_empty() && !name.starts_with('-') && name.chars().all(|c| c.is_ascii_alphanumeric() || "-_.".contains(c))
}

impl SshTarget {
    /// Parse `ALIAS,host=H,known_hosts=FILE[,user=U][,port=N][,identity=FILE][,jump=[U@]H[:P]][,become=U]...`.
    pub fn parse(grant: &str) -> Result<(String, SshTarget), String> {
        let mut parts = grant.split(',');
        let alias = parts.next().unwrap_or("");
        if !plain_name(alias) { return Err("SSH alias must be nonempty letters, digits, `-`, `_` or `.`".into()); }
        let mut target = SshTarget::default();
        let mut known_hosts = None;
        for part in parts {
            let (key, value) = part.split_once('=').ok_or_else(|| format!("SSH grant field `{part}` must be key=value"))?;
            if value.is_empty() { return Err(format!("SSH grant field `{key}` is empty")); }
            match key {
                "host" => target.host = value.into(),
                "user" => target.user = Some(value.into()),
                "port" => target.port = Some(value.parse().ok().filter(|port| *port > 0).ok_or("SSH port must be 1..65535")?),
                "known_hosts" => known_hosts = Some(PathBuf::from(value)),
                "identity" => target.identity = Some(value.into()),
                "jump" => target.jump = Some(value.into()),
                "become" => { target.become_users.insert(value.into()); }
                _ => return Err(format!("unknown SSH grant field `{key}`")),
            }
        }
        target.known_hosts = known_hosts.ok_or("SSH grant requires known_hosts=FILE; host keys are never trusted on first use")?;
        if target.host.is_empty() { return Err("SSH grant requires host=HOST".into()); }
        for (name, value) in [("host", Some(&target.host)), ("user", target.user.as_ref()), ("jump", target.jump.as_ref())] {
            let Some(value) = value else { continue };
            if value.starts_with('-') || value.chars().any(|c| c.is_whitespace() || c.is_control()) {
                return Err(format!("SSH {name} contains option-like or whitespace characters"));
            }
        }
        if let Some(jump) = &target.jump {
            let (user, host, port) = split_jump(jump);
            if host.is_empty() || user.is_some_and(str::is_empty) || port.is_some_and(|port| port.parse::<u16>().map_or(true, |port| port == 0)) {
                return Err("SSH jump must be [USER@]HOST[:PORT]".into());
            }
        }
        if !target.become_users.iter().all(|user| plain_name(user)) {
            return Err("SSH become user must be a plain account name".into());
        }
        Ok((alias.into(), target))
    }
}

/// POSIX single-quote one word so the remote login shell sees exactly the bytes supplied.
pub(super) fn quote(word: &str) -> String { format!("'{}'", word.replace('\'', "'\\''")) }

/// Caller-owned multiplexing state: sockets live in one private directory and persist briefly.
#[derive(Clone, Copy, Default)]
pub(super) struct Mux<'a> { pub dir: Option<&'a Path> }

/// Verification and authentication options every hop uses, so a jump host is held to the same policy as the target.
fn hardened(target: &SshTarget) -> Vec<String> {
    let mut argv: Vec<String> = ["-F", "/dev/null", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "GlobalKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10", "-o", "ClearAllForwardings=yes",
        "-o", "ForwardAgent=no", "-o", "ForwardX11=no", "-T"].map(String::from).into();
    argv.extend(["-o".into(), format!("UserKnownHostsFile={}", target.known_hosts.display())]);
    if let Some(identity) = &target.identity {
        argv.extend(["-o".into(), "IdentitiesOnly=yes".into(), "-i".into(), identity.display().to_string()]);
    }
    argv
}

/// `[USER@]HOST[:PORT]`, validated when the grant is parsed.
fn split_jump(spec: &str) -> (Option<&str>, &str, Option<&str>) {
    let (user, rest) = match spec.rsplit_once('@') { Some((user, rest)) => (Some(user), rest), None => (None, spec) };
    match rest.rsplit_once(':') { Some((host, port)) => (user, host, Some(port)), None => (user, rest, None) }
}

/// Options shared by every ssh invocation for a target, including the explicit `-O exit` teardown.
fn base_argv(target: &SshTarget, mux: Mux<'_>) -> Vec<String> {
    let mut argv = hardened(target);
    if let Some(user) = &target.user { argv.extend(["-l".into(), user.clone()]); }
    if let Some(port) = target.port { argv.extend(["-p".into(), port.to_string()]); }
    if let Some(jump) = &target.jump {
        // OpenSSH's own `-J` hop would ignore our host-key file, identity and BatchMode, so spell the hop out.
        let (user, host, port) = split_jump(jump);
        let mut hop: Vec<String> = hardened(target);
        if let Some(user) = user { hop.extend(["-l".into(), user.into()]); }
        if let Some(port) = port { hop.extend(["-p".into(), port.into()]); }
        hop.extend(["-W".into(), "%h:%p".into(), "--".into(), host.into()]);
        let hop = std::iter::once("ssh".to_string()).chain(hop).map(|word| quote(&word)).collect::<Vec<_>>().join(" ");
        argv.extend(["-o".into(), format!("ProxyCommand={hop}")]);
    }
    if mux.dir.is_some() {
        // Include the full grant as well as OpenSSH's target identity, so distinct authorities do not share a connection. The path is
        // relative to the child's working directory (the private socket directory) to stay under sun_path limits.
        use std::hash::{Hash, Hasher};
        let mut key = std::collections::hash_map::DefaultHasher::new();
        target.hash(&mut key);
        argv.extend(["-o".into(), "ControlMaster=auto".into(), "-o".into(),
            format!("ControlPath={:016x}-%C", key.finish()), "-o".into(), "ControlPersist=30s".into()]);
    }
    argv
}

/// One remote execution: quoting, escalation, optional stdin, and classification of ssh's own failures.
/// Runs on the remote host as `sh -c WRAPPER sh NONCE USER word...`. It reports what happened on a trailing
/// nonce-tagged completion line. This frames ordinary output, but is not a security boundary against
/// a hostile remote program inspecting its parent. The sudo shell reports command completion itself.
const WRAPPER: &str = r#"nonce=$1; user=$2; shift 2
if [ -n "$user" ]; then
  sudo -n -u "$user" -- sh -c 'nonce=$1; shift; "$@"; rc=$?; printf "\n%s exit %s\n" "$nonce" "$rc"' helix-ssh "$nonce" "$@"
  rc=$?; printf '\n%s privilege %s\n' "$nonce" "$rc"
else "$@"; rc=$?; printf '\n%s exit %s\n' "$nonce" "$rc"; fi"#;

fn nonce() -> Result<String, Failure> {
    use std::io::Read;
    let mut bytes = [0; 16];
    std::fs::File::open("/dev/urandom").and_then(|mut file| file.read_exact(&mut bytes)).map_err(super::failure::io_fault)?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

/// One remote execution: quoting, escalation, optional stdin, and classification of failures.
/// Anything after authentication is reported by the remote marker; only failures before the remote shell
/// runs (host key, authentication, connection) still come from ssh's own diagnostics.
fn execute<'a>(target: &SshTarget, mux: Mux<'a>, words: Vec<String>, become_user: Option<&str>, stdin: Option<&'a [u8]>,
               mut control: Control<'a>) -> Result<(i64, String, String), Failure> {
    if words.iter().any(|word| word.contains('\0')) { return Err(fault("type", "remote arguments cannot contain NUL")); }
    if let Some(user) = become_user {
        if !target.become_users.contains(user) {
            return Err(fault("capability", "SSH escalation identity not granted for this target"));
        }
    }
    let nonce = nonce()?;
    let mut remote_words = vec!["sh".to_string(), "-c".into(), WRAPPER.into(), "helix-ssh".into(), nonce.clone(), become_user.unwrap_or("").into()];
    remote_words.extend(words);
    let remote = remote_words.iter().map(|word| quote(word)).collect::<Vec<_>>().join(" ");
    let mut argv = base_argv(target, mux);
    argv.extend(["--".into(), target.host.clone(), remote]);
    let argv = argv.iter().map(String::as_str).collect::<Vec<_>>();
    control.cwd = mux.dir;
    control.stdin = stdin;
    command::capture_with("ssh", &argv, control, |(code, mut stdout, stderr)| {
        let marker = format!("\n{nonce} ");
        let Some(at) = stdout.find(&marker) else {
            if code == 255 { return Err(classify(&stderr)); }
            return Err(fault("ssh_remote", &format!("remote shell did not complete the request (ssh status {code}): {}", stderr.trim_end())));
        };
        if code != 0 { return Err(fault("ssh_connection", &format!("SSH failed after remote report (status {code}): {}", stderr.trim_end()))); }
        let report = stdout[at + marker.len()..].lines().next().unwrap_or("").to_string();
        stdout.truncate(at);
        match report.split_once(' ') {
            Some(("exit", status)) => {
                let code = status.parse::<i64>().ok().filter(|code| (0..=255).contains(code))
                    .ok_or_else(|| fault("ssh_remote", "malformed remote status"))?;
                Ok((code, stdout, stderr))
            },
            Some(("privilege", detail)) => Err(fault("ssh_privilege", &format!("sudo did not complete the remote wrapper (status {}): {}", detail.trim(), stderr.trim_end()))),
            _ => Err(fault("ssh_remote", "malformed remote report")),
        }
    })
}

fn object<const N: usize>(fields: [(&str, Yaml); N]) -> Yaml {
    Yaml::Hash(fields.into_iter().map(|(key, value)| (Yaml::String(key.into()), value)).collect())
}
fn string<'a>(payload: &'a Yaml, field: &str) -> Result<&'a str, Failure> {
    payload[field].as_str().ok_or_else(|| fault("type", &format!("ssh payload `{field}` must be a string")))
}
fn become_user(payload: &Yaml) -> Result<Option<&str>, Failure> {
    match &payload["become"] {
        Yaml::BadValue | Yaml::Null => Ok(None),
        Yaml::String(user) => Ok(Some(user)),
        _ => Err(fault("type", "ssh become must be a user name string")),
    }
}
/// Remote file operations take absolute paths so the login shell's working directory never matters.
fn remote_path(payload: &Yaml) -> Result<&str, Failure> {
    let path = string(payload, "path")?;
    if !path.starts_with('/') || path.contains('\0') { return Err(fault("type", "remote file path must be absolute")); }
    Ok(path)
}

pub(super) fn run(operation: &str, target: &SshTarget, mux: Mux<'_>, payload: &Yaml, control: Control<'_>) -> Result<Yaml, Failure> {
    let escalate = become_user(payload)?;
    match operation {
        "ssh_command" => {
            let bad_args = || fault("type", "ssh_command args must be a list of strings");
            let mut words = vec![string(payload, "program")?.to_string()];
            for arg in payload["args"].as_vec().ok_or_else(bad_args)? { words.push(arg.as_str().ok_or_else(bad_args)?.into()); }
            let stdin = match &payload["stdin"] {
                Yaml::BadValue | Yaml::Null => None,
                Yaml::String(text) if text.len() <= super::LIMIT => Some(text.as_bytes()),
                Yaml::String(_) => return Err(fault("resource", "ssh stdin exceeds 1 MiB")),
                _ => return Err(fault("type", "ssh_command stdin must be a string")),
            };
            let (code, stdout, stderr) = execute(target, mux, words, escalate, stdin, control)?;
            Ok(object([("code", Yaml::Integer(code)), ("stdout", Yaml::String(stdout)), ("stderr", Yaml::String(stderr))]))
        }
        "ssh_file_read" => {
            let path = remote_path(payload)?;
            let (code, stdout, stderr) = execute(target, mux, ["cat", "--", path].map(String::from).into(), escalate, None, control)?;
            if code != 0 { return Err(fault("ssh_file", stderr.trim_end())); }
            Ok(Yaml::String(stdout))
        }
        "ssh_file_write" => {
            let path = remote_path(payload)?;
            let text = string(payload, "text")?;
            if text.len() > super::LIMIT { return Err(fault("resource", "ssh file content exceeds 1 MiB")); }
            let mode = match &payload["mode"] {
                Yaml::BadValue | Yaml::Null => "0644",
                Yaml::String(mode) if (3..=4).contains(&mode.len()) && mode.chars().all(|c| ('0'..='7').contains(&c)) => mode,
                _ => return Err(fault("type", "ssh file mode must be an octal string such as \"0644\"")),
            };
            // Stage beside the target, verify the received byte count, then rename: readers never see a partial
            // file and a failed or truncated transfer leaves the original untouched. Not a multi-file transaction.
            const SCRIPT: &str = "set -eu; umask 077; tmp=$(mktemp -- \"$1.helix.XXXXXX\"); trap 'rm -f -- \"$tmp\"' EXIT; \
cat > \"$tmp\"; [ \"$(wc -c < \"$tmp\")\" -eq \"$3\" ]; chmod -- \"$2\" \"$tmp\"; mv -f -- \"$tmp\" \"$1\"; trap - EXIT";
            let words = ["sh", "-c", SCRIPT, "helix-write", path, mode, &text.len().to_string()].map(String::from).into();
            let (code, _, stderr) = execute(target, mux, words, escalate, Some(text.as_bytes()), control)?;
            if code != 0 { return Err(fault("ssh_file", stderr.trim_end())); }
            Ok(Yaml::Null)
        }
        _ => Err(fault("effect", &format!("unknown SSH operation `{operation}`"))),
    }
}

/// Tear down persistent masters at the end of a host session; failures only mean nothing was running.
pub(super) fn close_all(targets: &std::collections::BTreeMap<String, SshTarget>, dir: &Path) {
    for target in targets.values() {
        let mut argv = base_argv(target, Mux { dir: Some(dir) });
        argv.extend(["-O".into(), "exit".into(), "--".into(), target.host.clone()]);
        let _ = std::process::Command::new("ssh").args(&argv).current_dir(dir)
            .stdin(std::process::Stdio::null()).stdout(std::process::Stdio::null()).stderr(std::process::Stdio::null())
            .status();
    }
}

/// ssh reports host-key, authentication and connection failures alike as 255 before any remote shell runs, so these
/// pre-authentication causes are told apart from its diagnostics; unrecognized text stays a generic `ssh_connection`.
fn classify(stderr: &str) -> Failure {
    let message = stderr.trim_end();
    let kind = if ["Host key verification failed", "REMOTE HOST IDENTIFICATION HAS CHANGED", "No matching host key"]
        .iter().any(|needle| message.contains(needle)) { "ssh_host_key" }
    else if message.contains("Permission denied") || message.contains("No more authentication methods") { "ssh_auth" }
    else { "ssh_connection" };
    fault(kind, message)
}
