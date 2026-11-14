//! Caller-owned command policy and operating-system cancellation for explicit effects.
use std::path::Path;
use std::process::ExitCode;
use crate::{Status, Vm};

pub fn run(args: &[String]) -> ExitCode {
    use crate::EffectHost;
    let mut host = EffectHost::default();
    let mut progress_path = None;
    let mut checkpoint_path = None;
    let mut target_lock_path = None;
    let mut resume_checkpoint = None;
    let mut index = 0;
    while let Some(option) = args.get(index).filter(|arg| arg.starts_with("--")) {
        let takes_value = matches!(option.as_str(), "--allow-listen" | "--server-progress" | "--server-handlers" | "--server-connections" | "--server-deadline-ms" | "--allow-secret" | "--allow-read" | "--allow-write" | "--allow-env" | "--allow-command" | "--allow-http" | "--allow-ssh" | "--ssh-control-dir" | "--fixed-time" | "--seed" | "--command-timeout-ms" | "--command-output-limit" | "--command-attempts" | "--command-retry-delay-ms" | "--command-retry-exit-code" | "--command-progress" | "--target-lock" | "--checkpoint" | "--resume-checkpoint");
        let value = if takes_value {
            index += 1;
            let Some(value) = args.get(index) else { eprintln!("missing value for {option}"); return ExitCode::from(2); };
            value.as_str()
        } else { "" };
        match option.as_str() {
            "--command-timeout-ms" => match value.parse() {
                Ok(value) => host.command_policy.timeout_ms = value,
                Err(_) => return usage("command timeout must be unsigned integer milliseconds"),
            },
            "--command-output-limit" => match value.parse() {
                Ok(value) => host.command_policy.output_limit = value,
                Err(_) => return usage("command output limit must be unsigned integer bytes"),
            },
            "--command-attempts" => match value.parse() {
                Ok(value) => host.command_policy.max_attempts = value,
                Err(_) => return usage("command attempts must be an unsigned integer"),
            },
            "--command-retry-delay-ms" => match value.parse() {
                Ok(value) => host.command_policy.retry_delay_ms = value,
                Err(_) => return usage("command retry delay must be unsigned integer milliseconds"),
            },
            "--command-retry-exit-code" => match value.parse() {
                Ok(value) => { host.command_policy.retry_exit_codes.insert(value); }
                Err(_) => return usage("command retry exit code must be an integer"),
            },
            "--target-lock" => target_lock_path = Some(std::path::PathBuf::from(value)),
            "--checkpoint" => checkpoint_path = Some(std::path::PathBuf::from(value)),
            "--resume-checkpoint" => resume_checkpoint = Some(std::path::PathBuf::from(value)),
            "--command-progress" => progress_path = Some(std::path::PathBuf::from(value)),
            "--allow-secret" => match value.split_once('=') {
                Some((name, path)) if !name.is_empty() && !path.is_empty() => {
                    if host.secret_files.insert(name.into(), path.into()).is_some() {
                        return usage("duplicate secret grant");
                    }
                }
                _ => return usage("secret grant requires NAME=FILE"),
            },
            "--allow-listen" => match value.parse::<std::net::SocketAddr>() {
                Ok(address) if address.ip().is_loopback() => { host.listen_addresses.insert(address); }
                _ => return usage("listener grant must be an exact numeric loopback socket address"),
            },
            "--server-handlers" => match value.parse() { Ok(n) => host.server_policy.handlers = n, Err(_) => return usage("invalid handler capacity") },
            "--server-connections" => match value.parse() { Ok(n) => host.server_policy.connections = n, Err(_) => return usage("invalid connection capacity") },
            "--server-deadline-ms" => match value.parse() { Ok(n) => host.server_policy.deadline_ms = n, Err(_) => return usage("invalid request deadline") },
            "--server-progress" => {
                use std::io::Write;
                let mut file = match std::fs::OpenOptions::new().write(true).create_new(true).open(value) {
                    Ok(file) => file, Err(error) => return usage(&error.to_string()),
                };
                host.server_progress = Some(Box::new(move |event| {
                    writeln!(file, "{event}").and_then(|_| file.flush()).map_err(|error| ("io".into(), error.to_string()))?;
                    Ok(yaml_rust2::Yaml::Null)
                }));
            },
            "--allow-read" => host.read_roots.push(value.into()),
            "--allow-write" => host.write_roots.push(value.into()),
            "--allow-env" => { host.env_names.insert(value.into()); }
            "--allow-command" => { host.commands.insert(value.into()); }
            "--allow-http" => { host.http_origins.insert(value.into()); }
            "--allow-ssh" => match crate::SshTarget::parse(value) {
                Ok((alias, target)) => { host.ssh_targets.insert(alias, target); }
                Err(message) => return usage(&message),
            },
            "--ssh-control-dir" => match private_directory(Path::new(value)) {
                Ok(dir) => host.ssh_control_dir = Some(dir),
                Err(message) => return usage(&message),
            },
            "--allow-stdin" => host.allow_stdin = true,
            "--allow-stdout" => host.allow_stdout = true,
            "--allow-time" => host.allow_time = true,
            "--allow-random" => host.allow_random = true,
            "--fixed-time" => match value.parse() {
                Ok(value) => host.fixed_time = Some(value),
                Err(_) => { eprintln!("fixed time must be integer milliseconds"); return ExitCode::from(2); }
            },
            "--seed" => match value.parse() {
                Ok(value) => host.random_seed = Some(value),
                Err(_) => { eprintln!("seed must be an unsigned integer"); return ExitCode::from(2); }
            },
            "--help" => {
                println!("helix perform [grants] <file> [-- args...]\nGrants: --allow-secret NAME=FILE (private UTF-8 input; command output suppressed), --allow-read ROOT, --allow-write ROOT, --allow-env NAME, --allow-command EXECUTABLE, --allow-http ORIGIN, --allow-ssh ALIAS,host=H,known_hosts=FILE[,user=U][,port=N][,identity=FILE][,jump=J][,become=U], --ssh-control-dir PRIVATE_DIR (reuse connections), --allow-stdin, --allow-stdout, --allow-time, --allow-random\nDeterminism: --fixed-time MILLISECONDS, --seed INTEGER (still require grants)\nCommand policy (caller grants, not source): --command-timeout-ms N (1..86400000, default 10000; total across retries), --command-output-limit N (1..16777216 bytes per stream across retries, default 1048576), --command-attempts N (1..32, default 1), --command-retry-delay-ms N (0..60000), repeatable --command-retry-exit-code N (1..255; only explicitly classified exit codes retry)\n--command-progress NEW_FILE writes versioned JSONL metadata, never command output; existing files are refused. SIGINT/SIGTERM cancel commands and clean up their Unix process group.\n--target-lock FILE coordinates cooperating controllers across journals; crashes/errors retain a marker until explicit helix target-release FILE after independent quiescence/reconciliation. All controllers must use the same path; this is not a remote fencing service.\n--checkpoint NEW_FILE journals before and after each effect; --resume-checkpoint FILE restores with fresh grants and refuses uncertain outcomes. helix reconcile FILE OUTCOME records an independently observed outcome for the exact uncertain request.\nProgram stdout is stdout; full state is stderr. exit effect chooses successful process exit code.");
                return ExitCode::SUCCESS;
            }
            _ => { eprintln!("unknown perform option {option}"); return ExitCode::from(2); }
        }
        index += 1;
    }
    if let Err((_, message)) = host.command_policy.validate() { return usage(&message); }
    if checkpoint_path.is_some() && resume_checkpoint.is_some() { return usage("choose --checkpoint or --resume-checkpoint"); }
    let mut restored = None;
    let checkpoint = if let Some(path) = resume_checkpoint {
        match crate::vm::effects::durable::Checkpoint::resume(&path) {
            Ok((checkpoint, vm)) => { restored = Some(vm); Some(checkpoint) },
            Err(error) => return checkpoint_error(&error),
        }
    } else if let Some(path) = checkpoint_path {
        match crate::vm::effects::durable::Checkpoint::create(&path) {
            Ok(checkpoint) => Some(checkpoint), Err(error) => return checkpoint_error(&error),
        }
    } else { None };
    let mut vm = if let Some(vm) = restored { vm } else {
        let Some(path) = args.get(index) else { return usage("perform requires a source file"); };
        index += 1;
        let source = match std::fs::read_to_string(path) {
            Ok(source) => source, Err(error) => return usage(&format!("cannot read {path}: {error}")),
        };
        Vm::load_sandboxed(&source, Some(Path::new(path)), &host.read_roots)
    };
    let target_guard = if let Some(path) = target_lock_path {
        match crate::vm::effects::locks::TargetGuard::acquire(&path) {
            Ok(guard) => Some(guard), Err(error) => return checkpoint_error(&error),
        }
    } else { None };
    if args.get(index).is_some_and(|arg| arg == "--") { index += 1; }
    host.args = args[index..].to_vec();
    if let Some(path) = progress_path {
        host.command_progress = Some(progress_sink(path));
    }
    let _ssh_session = if let Some(root) = &host.ssh_control_dir {
        match SshSession::create(root) {
            Ok(session) => { host.ssh_control_dir = Some(session.0.clone()); Some(session) },
            Err(error) => return usage(&error),
        }
    } else { None };
    #[cfg(unix)]
    let _signals = match signals::Guard::install() {
        Ok(guard) => { host.cancellation_signal = Some(&signals::CANCELLED); guard }
        Err(error) => return usage(&format!("cannot install cancellation handlers: {error}")),
    };
    if let Some(checkpoint) = &checkpoint {
        if let Err(error) = host.run_checkpointed(&mut vm, checkpoint) {
            host.close_ssh_connections();
            return checkpoint_error(&error);
        }
    } else { host.run(&mut vm); }
    host.close_ssh_connections();
    if let Some(guard) = target_guard {
        let state = vm.state();
        let clean = vm.status() == Status::Finished
            && state["effects"].as_vec().is_none_or(|effects|
                effects.iter().all(|effect| effect["status"].as_str() == Some("finished")));
        if clean {
            if let Err(error) = guard.release() { return checkpoint_error(&error); }
        }
        // Errors and interruptions deliberately retain the uncertainty marker.
    }
    eprint!("{}", vm.state_string());
    let cancelled = host.cancellation.load(std::sync::atomic::Ordering::Relaxed)
        || host.cancellation_signal.is_some_and(|flag| flag.load(std::sync::atomic::Ordering::Relaxed));
    if vm.status() == Status::Finished && !cancelled { ExitCode::from(host.exit_code) } else { ExitCode::FAILURE }
}

/// Connection-reuse sockets grant their holder the authenticated session, so the directory must be owner-only.
fn private_directory(path: &Path) -> Result<std::path::PathBuf, String> {
    use std::os::unix::fs::{DirBuilderExt, MetadataExt, PermissionsExt};
    std::fs::DirBuilder::new().recursive(true).mode(0o700).create(path)
        .map_err(|error| format!("cannot create SSH control directory {}: {error}", path.display()))?;
    let meta = std::fs::metadata(path).map_err(|error| error.to_string())?;
    if !meta.is_dir() || meta.permissions().mode() & 0o077 != 0 || meta.uid() != unsafe { libc::geteuid() } {
        return Err(format!("SSH control directory {} must be a directory accessible only by its owner", path.display()));
    }
    path.canonicalize().map_err(|error| error.to_string())
}

// A fresh host session never inherits an earlier controller's authenticated sockets.
struct SshSession(std::path::PathBuf);
impl SshSession {
    fn create(root: &Path) -> Result<Self, String> {
        use std::os::unix::fs::DirBuilderExt;
        let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH)
            .map_err(|error| error.to_string())?.as_nanos();
        let path = root.join(format!("session-{}-{stamp}", std::process::id()));
        std::fs::DirBuilder::new().mode(0o700).create(&path).map_err(|error| error.to_string())?;
        Ok(Self(path))
    }
}
impl Drop for SshSession {
    fn drop(&mut self) { let _ = std::fs::remove_dir(&self.0); }
}

fn usage(message: &str) -> ExitCode {
    eprintln!("{message}; see helix perform --help");
    ExitCode::from(2)
}

fn progress_sink(path: std::path::PathBuf) -> crate::ProgressCallback {
    use std::io::Write;
    let mut file = None;
    Box::new(move |event| {
        let mut write = || -> std::io::Result<()> {
            if file.is_none() {
                file = Some(std::fs::OpenOptions::new().write(true).create_new(true).open(&path)?);
            }
            let record = serde_json::json!({
                "version": 1, "effect_id": event.effect_id, "attempt": event.attempt,
                "event": event.event, "elapsed_ms": event.elapsed_ms,
                "stdout_bytes": event.stdout_bytes, "stderr_bytes": event.stderr_bytes,
                "code": event.code,
            });
            let output = file.as_mut().unwrap();
            writeln!(output, "{record}")?;
            output.flush()
        };
        write().map_err(|error| ("io".into(), format!("command progress: {error}")))
    })
}

#[cfg(unix)]
mod signals {
    use std::sync::atomic::{AtomicBool, Ordering};
    pub static CANCELLED: AtomicBool = AtomicBool::new(false);
    static INSTALLED: AtomicBool = AtomicBool::new(false);

    extern "C" fn cancel(_: libc::c_int) {
        CANCELLED.store(true, Ordering::Relaxed);
    }

    pub struct Guard { previous: [libc::sigaction; 2] }
    impl Guard {
        pub fn install() -> std::io::Result<Self> {
            if INSTALLED.swap(true, Ordering::AcqRel) {
                return Err(std::io::Error::other("perform cancellation handlers are already installed"));
            }
            CANCELLED.store(false, Ordering::Relaxed);
            // The handler only writes static atomic storage. Restore prior dispositions on exit.
            unsafe {
                let mut action: libc::sigaction = std::mem::zeroed();
                action.sa_sigaction = cancel as *const () as usize;
                libc::sigemptyset(&mut action.sa_mask);
                let mut previous: [libc::sigaction; 2] = std::mem::zeroed();
                for (index, signal) in [libc::SIGINT, libc::SIGTERM].into_iter().enumerate() {
                    if libc::sigaction(signal, &action, &mut previous[index]) != 0 {
                        let error = std::io::Error::last_os_error();
                        if index == 1 { libc::sigaction(libc::SIGINT, &previous[0], std::ptr::null_mut()); }
                        INSTALLED.store(false, Ordering::Release);
                        return Err(error);
                    }
                }
                Ok(Self { previous })
            }
        }
    }
    impl Drop for Guard {
        fn drop(&mut self) {
            unsafe {
                for (signal, previous) in [libc::SIGINT, libc::SIGTERM].into_iter().zip(&self.previous) {
                    libc::sigaction(signal, previous, std::ptr::null_mut());
                }
            }
            INSTALLED.store(false, Ordering::Release);
        }
    }
}

fn checkpoint_error(message: &str) -> ExitCode {
    eprintln!("{}", serde_json::json!({"status":"error","errors":[{"kind":"checkpoint","message":message}]}));
    ExitCode::FAILURE
}
