//! Public snapshot contracts, run through manage.py test --suite library.
use helix::{Status, Vm};
fn run(mut vm: Vm) -> Vm {
    vm.run();
    vm
}
fn every_boundary(source: &str) {
    let original = Vm::load(source);
    let expected = run(original.clone()).state_string();
    let mut vm = original;
    for count in 0..20_000 {
        let saved = vm.snapshot().expect("encode");
        let restored = Vm::restore(&saved).unwrap_or_else(|e| panic!("boundary {count}: {e}"));
        assert_eq!(
            restored.state_string(),
            vm.state_string(),
            "boundary {count}"
        );
        assert_eq!(
            run(restored.clone()).state_string(),
            expected,
            "finish from {count}"
        );
        assert_eq!(
            restored.snapshot().unwrap(),
            saved,
            "stable serialization at {count}"
        );
        if !matches!(vm.status(), Status::Ready | Status::Running) {
            return;
        }
        vm = vm.step();
    }
    panic!("test exceeds transition bound")
}
#[test]
fn nested_calls_captures_annotations_and_iteration() {
    every_boundary(
        r#"{$call: [let, {base: 3, f: {$call: [fn, [[x, integer]], {$call: [add, {$ref: x}, {$ref: base}]}, integer]}}, {$call: [map, {$ref: f}, [1, 2, 3]]}]}"#,
    );
}
#[test]
fn handlers_refinements_and_child_machines() {
    for source in [
        r#"{$call: [div, 1, 0]}"#,
        r#"{$call: [try, {$call: [div, 1, 0]}, {$call: [fn, [e], 42]}]}"#,
        r#"{$call: [expect, 4, {$call: [refine, integer, {$call: [fn, [n], {$call: [gt, {$ref: n}, 0]}]}]}]}"#,
        r#"{$call: [step, {$call: [vm, {$call: [fn, [], {$call: [add, 1, 2]}]}]}]}"#,
        r#"{$call: [type_time, {$call: [add, 1, 2]}]}"#,
    ] {
        every_boundary(source);
    }
}
#[test]
fn effects_and_interruptions_retain_completed_outcomes() {
    let vm = run(Vm::load(
        r#"[{$call: [effect, clock, null]}, {$call: [effect, random, null]} ]"#,
    ));
    let saved = vm.snapshot().unwrap();
    let restored = Vm::restore(&saved).unwrap();
    assert_eq!(restored.pending_effect(), vm.pending_effect());
    let completed = run(restored.resolve_effect(Ok(yaml_rust2::Yaml::Integer(17))));
    assert_eq!(completed.pending_effect().unwrap().id, 2);
    let restored = Vm::restore(&completed.snapshot().unwrap()).unwrap();
    assert_eq!(restored.pending_effect().unwrap().id, 2);
    let done = run(restored.resolve_effect(Ok(yaml_rust2::Yaml::Integer(29))));
    assert!(done.state_string().contains("17"));
    assert_eq!(done.status(), Status::Finished);
    let interrupted = Vm::load("42").signal(yaml_rust2::Yaml::String("pause".into()));
    let restored = Vm::restore(&interrupted.snapshot().unwrap()).unwrap();
    assert!(restored.is_interrupted());
    assert_eq!(restored.status(), Status::Signaled);
    assert_eq!(run(restored.resume_signal()).status(), Status::Finished);
}
#[test]
fn malformed_version_and_references_are_rejected() {
    let saved = Vm::load("42").snapshot().unwrap();
    let mut doc: serde_json::Value = serde_json::from_str(&saved).unwrap();
    doc["version"] = 99.into();
    assert!(Vm::restore(&doc.to_string()).is_err());
    doc["version"] = 1.into();
    doc["machine"] = "missing".into();
    assert!(Vm::restore(&doc.to_string()).is_err());
    assert!(Vm::restore(&run(Vm::load("42")).state_string()).is_err());
}

#[test]
fn expanded_source_references_obey_the_snapshot_budget() {
    // A small source can otherwise expand dramatically when a long ancestor key
    // occurs in every saved path. Saving must stop while constructing the graph.
    let source = format!("? \"{}\"\n: [{}]", "k".repeat(16_000), vec!["0"; 2500].join(","));
    let vm = Vm::load(&source);
    assert_eq!(vm.status(), Status::Ready);
    assert!(vm.snapshot().unwrap_err().contains("budget"));
    assert_eq!(vm.status(), Status::Ready);
}
