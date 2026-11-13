//! Remote commands over the system OpenSSH client. Grants name exact targets; source cannot
//! choose a destination, key, host-key policy or escalation identity.
use std::collections::BTreeSet;
use std::path::PathBuf;
use yaml_rust2::Yaml;
use super::command::{self, Control};
use super::failure::{Failure, fault};

/// One caller-authorized remote target, addressed from source only by its alias.
#[derive(Clone, Debug, Default)]
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
        if !target.become_users.iter().all(|user| plain_name(user)) {
            return Err("SSH become user must be a plain account name".into());
        }
        Ok((alias.into(), target))
    }
}

/// POSIX single-quote one word so the remote login shell sees exactly the bytes supplied.
pub(super) fn quote(word: &str) -> String { format!("'{}'", word.replace('\'', "'\\''")) }

pub(super) fn run(target: &SshTarget, payload: &Yaml, control: Control<'_>) -> Result<Yaml, Failure> {
    let bad_args = || fault("type", "ssh_command args must be a list of strings");
    let program = payload["program"].as_str().ok_or_else(|| fault("type", "ssh_command program must be a string"))?;
    let mut words = vec![program.to_string()];
    for arg in payload["args"].as_vec().ok_or_else(bad_args)? { words.push(arg.as_str().ok_or_else(bad_args)?.into()); }
    if words.iter().any(|word| word.contains('\0')) { return Err(fault("type", "remote arguments cannot contain NUL")); }
    let escalated = match &payload["become"] {
        Yaml::BadValue | Yaml::Null => false,
        Yaml::String(user) => {
            if !target.become_users.contains(user) {
                return Err(fault("capability", "SSH escalation identity not granted for this target"));
            }
            words.splice(0..0, ["sudo", "-n", "-u", user, "--"].map(String::from));
            true
        }
        _ => return Err(fault("type", "ssh_command become must be a user name string")),
    };
    let remote = words.iter().map(|word| quote(word)).collect::<Vec<_>>().join(" ");
    let mut argv: Vec<String> = ["-F", "/dev/null", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
        "-o", "GlobalKnownHostsFile=/dev/null", "-o", "ConnectTimeout=10", "-o", "ClearAllForwardings=yes",
        "-o", "ForwardAgent=no", "-o", "ForwardX11=no", "-T"].map(String::from).into();
    argv.extend(["-o".into(), format!("UserKnownHostsFile={}", target.known_hosts.display())]);
    if let Some(identity) = &target.identity {
        argv.extend(["-o".into(), "IdentitiesOnly=yes".into(), "-i".into(), identity.display().to_string()]);
    }
    if let Some(user) = &target.user { argv.extend(["-l".into(), user.clone()]); }
    if let Some(port) = target.port { argv.extend(["-p".into(), port.to_string()]); }
    if let Some(jump) = &target.jump { argv.extend(["-J".into(), jump.clone()]); }
    argv.extend(["--".into(), target.host.clone(), remote]);
    let argv = argv.iter().map(String::as_str).collect::<Vec<_>>();
    let (code, stdout, stderr) = command::capture("ssh", &argv, control)?;
    if code == 255 { return Err(classify(&stderr)); }
    // Sudo diagnoses its own refusal on stderr and exits 1; a remote program could imitate it,
    // so this is a best-effort distinction between privilege and program failure.
    if escalated && code == 1 && stderr.starts_with("sudo: ") { return Err(fault("ssh_privilege", stderr.trim_end())); }
    Ok(Yaml::Hash([("code", Yaml::Integer(code)), ("stdout", Yaml::String(stdout)), ("stderr", Yaml::String(stderr))]
        .into_iter().map(|(key, value)| (Yaml::String(key.into()), value)).collect()))
}

/// ssh reserves exit 255 for its own failures; separate the causes a caller can act on.
fn classify(stderr: &str) -> Failure {
    let message = stderr.trim_end();
    let kind = if ["Host key verification failed", "REMOTE HOST IDENTIFICATION HAS CHANGED", "No matching host key"]
        .iter().any(|needle| message.contains(needle)) { "ssh_host_key" }
    else if message.contains("Permission denied") || message.contains("No more authentication methods") { "ssh_auth" }
    else { "ssh_connection" };
    fault(kind, message)
}
