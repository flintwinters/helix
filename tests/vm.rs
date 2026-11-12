//! Public API contracts: no evaluator internals or CLI serialization shortcuts.
use helix::{Status, Vm};

#[test]
fn independent_instances_and_repeated_runs() {
    let mut first = Vm::load(r##"{"answer": {"$call": ["add", 20, 22]}}"##);
    let mut second = Vm::load(r##"{"answer": {"$call": ["mul", 6, 7]}}"##);
    assert_eq!(first.status(), Status::Ready);
    assert_eq!(first.state()["status"].as_str(), Some("ready"));
    second.run();
    assert_eq!(first.status(), Status::Ready);
    first.run();
    assert_eq!(first.status(), Status::Finished);
    assert_eq!(first.state(), second.state());
    let saved = first.state_string();
    first.run();
    assert_eq!(saved, first.state_string());
}

#[test]
fn errors_are_terminal_and_independent() {
    for source in ["[", r##"{"answer": {"$call": ["div", 1, 0]}}"##] {
        let mut broken = Vm::load(source);
        broken.run();
        assert_eq!(broken.status(), Status::Error);
        let saved = broken.state();
        assert!(saved["error"]["kind"].as_str().is_some());
        broken.run();
        assert_eq!(saved, broken.state());
        let mut valid = Vm::load("42");
        valid.run();
        assert_eq!(valid.state()["root"].as_i64(), Some(42));
    }
}

#[test]
fn check_collects_errors_and_preserves_success() {
    let mut checked = Vm::load(r##"{"a": {"$call": ["div", 1, 0]}, "b": {"$call": ["add", {"$ref": "missing"}, 1]}}"##);
    checked.check();
    assert_eq!(checked.status(), Status::Error);
    assert_eq!(checked.state()["mode"].as_str(), Some("check"));
    assert!(checked.state()["phase"].is_badvalue());
    assert_eq!(checked.state()["errors"].as_vec().unwrap().len(), 2);
    let mut clean = Vm::load(r##"{"answer": {"$call": ["add", 1, 2]}}"##);
    clean.check();
    assert_eq!(clean.status(), Status::Finished);
    assert_eq!(clean.state()["root"]["answer"].as_i64(), Some(3));
    assert_eq!(clean.state()["mode"].as_str(), Some("check"));
    assert!(clean.state()["phase"].is_badvalue());
}

#[test]
fn terminal_execution_survives_check_and_repeated_checks() {
    for source in ["answer: 42", r##"{"answer": {"$call": ["div", 1, 0]}}"##] {
        let mut vm = Vm::load(source);
        vm.run();
        let saved = vm.state_string();
        vm.check();
        assert_eq!(vm.state_string(), saved);
    }
    for source in ["answer: 42", r##"{"answer": {"$call": ["div", 1, 0]}}"##, "["] {
        let mut vm = Vm::load(source);
        vm.check();
        let saved = vm.state_string();
        assert_eq!(vm.state()["mode"].as_str(), Some("check"));
        assert!(vm.state()["phase"].is_badvalue());
        vm.check();
        vm.run();
        assert_eq!(vm.state_string(), saved);
    }
}

#[test]
fn resource_guards_apply_to_library_callers() {
    for (source, kind) in [
        ("[".repeat(65) + "0" + &"]".repeat(65), "depth"),
        (
            r##"{"$call": ["until", {"$call": ["fn", ["s"], false]}, {"$call": ["fn", ["s"], {"$call": ["concat", {"$ref": "s"}, {"$ref": "s"}]}]}, "x"]}"##.into(),
            "limit",
        ),
    ] {
        let mut vm = Vm::load(&source);
        vm.run();
        assert_eq!(vm.status(), Status::Error);
        assert_eq!(vm.state()["error"]["kind"].as_str(), Some(kind));
    }
    let mut recursion =
        Vm::load(r##"{"f": {"$call": ["fn", ["n"], {"$call": ["if", {"$call": ["eq", {"$ref": "n"}, 0]}, 0, {"$call": ["f", {"$call": ["sub", {"$ref": "n"}, 1]}]}]}]}, "r": {"$call": ["f", 200]}}"##);
    recursion.run();
    assert_eq!(recursion.status(), Status::Error);
    assert_eq!(recursion.state()["error"]["kind"].as_str(), Some("depth"));
}

#[test]
fn embedded_only_loading_does_not_grant_file_access() {
    let mut denied = Vm::load_sandboxed(r##"{"m": {"$call": ["import", "Cargo.toml"]}}"##, None, &[]);
    assert_eq!(denied.status(), Status::Error);
    assert_eq!(denied.state()["error"]["kind"].as_str(), Some("permission"));
    let mut allowed = Vm::load_sandboxed(r##"{"m": {"$call": ["import", "std:math"]}, "r": {"$call": ["m.square", 3]}}"##, None, &[]);
    allowed.run();
    assert_eq!(allowed.state()["root"]["r"].as_i64(), Some(9));
    denied.run();
    assert_eq!(denied.status(), Status::Error);
}

#[cfg(unix)]
#[test]
fn host_cancellation_blocks_caught_followup_effects_until_explicit_resume() {
    use std::sync::atomic::Ordering;
    let mut host = helix::EffectHost::default();
    host.commands.insert("/bin/sleep".into());
    host.command_policy = helix::CommandPolicy { timeout_ms: 2_000, ..Default::default() };
    host.allow_time = true;
    host.fixed_time = Some(42);
    let cancellation = host.cancellation.clone();
    host.command_progress = Some(Box::new(move |event: &helix::CommandProgress| {
        if event.event == "started" { cancellation.store(true, Ordering::Relaxed); }
        Ok(())
    }));
    let mut vm = Vm::load(r#"{"$call": ["try", {"$call": ["effect", "command", {"program": "/bin/sleep", "args": ["30"]}]}, {"$call": ["fn", ["error"], {"$call": ["effect", "time", null]}]}]}"#);
    host.run(&mut vm);
    assert_eq!(vm.status(), Status::Signaled);
    assert!(vm.is_interrupted());
    assert_eq!(vm.pending_effect().unwrap().operation, "time");
    assert_eq!(vm.state()["effects"].as_vec().unwrap().len(), 1);
    assert_eq!(vm.state()["signal"]["kind"].as_str(), Some("cancelled"));
    let interrupted = vm.state_string();
    host.run(&mut vm);
    assert_eq!(vm.state_string(), interrupted);
    // Clearing the host token alone does not erase the machine's explicit pause.
    host.cancellation.store(false, Ordering::Relaxed);
    host.run(&mut vm);
    assert_eq!(vm.state_string(), interrupted);
    vm = vm.resume_signal();
    host.run(&mut vm);
    assert_eq!(vm.status(), Status::Finished);
    assert_eq!(vm.state()["root"].as_i64(), Some(42));
    assert_eq!(vm.state()["effects"].as_vec().unwrap().len(), 2);
}

#[cfg(unix)]
#[test]
fn failing_or_panicking_progress_callbacks_reap_running_commands() {
    use std::sync::{Arc, atomic::{AtomicI32, Ordering}};
    use std::panic::{AssertUnwindSafe, catch_unwind};
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("runtime")
        .join(format!("host-callback-{}", std::process::id()));
    std::fs::create_dir_all(&root).unwrap();
    for panic in [false, true] {
        let path = root.join(if panic { "panic.pid" } else { "failure.pid" });
        let observed_pid = Arc::new(AtomicI32::new(0));
        let callback_pid = observed_pid.clone();
        let callback_path = path.clone();
        let mut host = helix::EffectHost::default();
        host.commands.insert("/bin/sh".into());
        host.command_progress = Some(Box::new(move |event| {
            if event.event == "output" {
                let pid = std::fs::read_to_string(&callback_path).unwrap().parse().unwrap();
                callback_pid.store(pid, Ordering::Relaxed);
                assert!(!panic, "intentional progress callback panic");
                return Err(("progress_test".into(), "intentional callback failure".into()));
            }
            Ok(())
        }));
        let source = serde_json::json!({"$call": ["effect", "command", {
            "program": "/bin/sh", "args": ["-c", "printf '%s' \"$$\" > \"$1\"; printf ready; sleep 30", "helix-test", path]
        }]}).to_string();
        let mut vm = Vm::load(&source);
        let result = catch_unwind(AssertUnwindSafe(|| host.run(&mut vm)));
        assert_eq!(result.is_err(), panic);
        if !panic {
            assert_eq!(vm.status(), Status::Error);
            assert_eq!(vm.state()["error"]["kind"].as_str(), Some("progress_test"));
        }
        let pid = observed_pid.load(Ordering::Relaxed);
        assert!(pid > 0, "callback must observe a running command");
        // SAFETY: signal zero only checks existence; it does not signal another process.
        assert_eq!(unsafe { libc::kill(pid, 0) }, -1, "command survived callback failure");
        assert_eq!(std::io::Error::last_os_error().raw_os_error(), Some(libc::ESRCH));
    }
    std::fs::remove_dir_all(root).unwrap();
}
