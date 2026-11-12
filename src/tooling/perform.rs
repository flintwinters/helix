//! Caller-owned command policy and operating-system cancellation for explicit effects.
use std::path::Path;
use std::process::ExitCode;
use crate::{Status, Vm};

pub fn run(args: &[String]) -> ExitCode {
    use crate::EffectHost;
    let mut host = EffectHost::default();
    let mut progress_path = None;
    let mut index = 0;
    while let Some(option) = args.get(index).filter(|arg| arg.starts_with("--")) {
        let takes_value = matches!(option.as_str(), "--allow-read" | "--allow-write" | "--allow-env" | "--allow-command" | "--allow-http" | "--fixed-time" | "--seed" | "--command-timeout-ms" | "--command-output-limit" | "--command-attempts" | "--command-retry-delay-ms" | "--command-retry-exit-code" | "--command-progress");
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
            "--command-progress" => progress_path = Some(std::path::PathBuf::from(value)),
            "--allow-read" => host.read_roots.push(value.into()),
            "--allow-write" => host.write_roots.push(value.into()),
            "--allow-env" => { host.env_names.insert(value.into()); }
            "--allow-command" => { host.commands.insert(value.into()); }
            "--allow-http" => { host.http_origins.insert(value.into()); }
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
                println!("helix perform [grants] <file> [-- args...]\nGrants: --allow-read ROOT, --allow-write ROOT, --allow-env NAME, --allow-command EXECUTABLE, --allow-http ORIGIN, --allow-stdin, --allow-stdout, --allow-time, --allow-random\nDeterminism: --fixed-time MILLISECONDS, --seed INTEGER (still require grants)\nCommand policy (caller grants, not source): --command-timeout-ms N (1..86400000, default 10000; total across retries), --command-output-limit N (1..16777216 bytes per stream across retries, default 1048576), --command-attempts N (1..32, default 1), --command-retry-delay-ms N (0..60000), repeatable --command-retry-exit-code N (1..255; only explicitly classified exit codes retry)\n--command-progress NEW_FILE writes versioned JSONL metadata, never command output; existing files are refused. SIGINT/SIGTERM cancel commands and clean up their Unix process group.\nProgram stdout is stdout; full state is stderr. exit effect chooses successful process exit code.");
                return ExitCode::SUCCESS;
            }
            _ => { eprintln!("unknown perform option {option}"); return ExitCode::from(2); }
        }
        index += 1;
    }
    if let Err((_, message)) = host.command_policy.validate() { return usage(&message); }
    let Some(path) = args.get(index) else { eprintln!("perform requires a source file"); return ExitCode::from(2); };
    index += 1;
    if args.get(index).is_some_and(|arg| arg == "--") { index += 1; }
    host.args = args[index..].to_vec();
    let source = match std::fs::read_to_string(path) {
        Ok(source) => source,
        Err(error) => { eprintln!("cannot read {path}: {error}"); return ExitCode::from(2); }
    };
    if let Some(path) = progress_path {
        host.command_progress = Some(progress_sink(path));
    }
    #[cfg(unix)]
    let _signals = match signals::Guard::install() {
        Ok(guard) => { host.cancellation_signal = Some(&signals::CANCELLED); guard }
        Err(error) => return usage(&format!("cannot install cancellation handlers: {error}")),
    };
    let mut vm = Vm::load_sandboxed(&source, Some(Path::new(path)), &host.read_roots);
    host.run(&mut vm);
    eprint!("{}", vm.state_string());
    let cancelled = host.cancellation.load(std::sync::atomic::Ordering::Relaxed)
        || host.cancellation_signal.is_some_and(|flag| flag.load(std::sync::atomic::Ordering::Relaxed));
    if vm.status() == Status::Finished && !cancelled { ExitCode::from(host.exit_code) } else { ExitCode::FAILURE }
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
