//! Public API contracts: no evaluator internals or CLI serialization shortcuts.
use helix::{Status, Vm};

#[test]
fn independent_instances_and_repeated_runs() {
    let mut first = Vm::load("answer: [add, 20, 22]");
    let mut second = Vm::load("answer: [mul, 6, 7]");
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
    for source in ["[", "answer: [div, 1, 0]"] {
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
    let mut checked = Vm::load("a: [div, 1, 0]\nb: [add, missing, 1]");
    checked.check();
    assert_eq!(checked.status(), Status::Error);
    assert_eq!(checked.state()["phase"].as_str(), Some("type"));
    assert_eq!(checked.state()["errors"].as_vec().unwrap().len(), 2);
    let mut clean = Vm::load("answer: [add, 1, 2]");
    clean.check();
    assert_eq!(clean.status(), Status::Finished);
    assert_eq!(clean.state()["root"]["answer"].as_i64(), Some(3));
}

#[test]
fn terminal_execution_survives_check_and_repeated_checks() {
    for source in ["answer: 42", "answer: [div, 1, 0]"] {
        let mut vm = Vm::load(source);
        vm.run();
        let saved = vm.state_string();
        vm.check();
        assert_eq!(vm.state_string(), saved);
    }
    for source in ["answer: 42", "answer: [div, 1, 0]", "["] {
        let mut vm = Vm::load(source);
        vm.check();
        let saved = vm.state_string();
        assert_eq!(vm.state()["phase"].as_str(), Some("type"));
        vm.check();
        vm.run();
        assert_eq!(vm.state_string(), saved);
    }
}

#[test]
fn resource_guards_apply_to_library_callers() {
    for (source, kind) in [
        ("[".repeat(65) + "0" + &"]".repeat(65), "depth"),
        ("[until, [fn, [s], false], [fn, [s], [concat, s, s]], \"x\"]".into(), "limit"),
    ] {
        let mut vm = Vm::load(&source);
        vm.run();
        assert_eq!(vm.status(), Status::Error);
        assert_eq!(vm.state()["error"]["kind"].as_str(), Some(kind));
    }
    let mut recursion = Vm::load("f: [fn, [n], [if, [eq, n, 0], 0, [f, [sub, n, 1]]]]\nr: [f, 200]");
    recursion.run();
    assert_eq!(recursion.status(), Status::Error);
    assert_eq!(recursion.state()["error"]["kind"].as_str(), Some("depth"));
}

#[test]
fn embedded_only_loading_does_not_grant_file_access() {
    let mut denied = Vm::load_sandboxed("m: [import, Cargo.toml]", None, &[]);
    assert_eq!(denied.status(), Status::Error);
    assert_eq!(denied.state()["error"]["kind"].as_str(), Some("permission"));
    let mut allowed = Vm::load_sandboxed("m: [import, std:math]\nr: [m.square, 3]", None, &[]);
    allowed.run();
    assert_eq!(allowed.state()["root"]["r"].as_i64(), Some(9));
    denied.run();
    assert_eq!(denied.status(), Status::Error);
}
