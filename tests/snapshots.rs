//! Public snapshot contracts, run through manage.py test --suite library.
use helix::{Status, Vm};
use yaml_rust2::Yaml;

/// Snapshots are written as YAML; tests edit them as JSON value trees.
fn tree(snapshot: &str) -> serde_json::Value {
    fn convert(y: &Yaml) -> serde_json::Value {
        match y {
            Yaml::Null => serde_json::Value::Null,
            Yaml::Boolean(b) => (*b).into(),
            Yaml::Integer(i) => (*i).into(),
            Yaml::String(s) => s.clone().into(),
            Yaml::Array(a) => a.iter().map(convert).collect(),
            Yaml::Hash(h) => h.iter().map(|(k, v)| (k.as_str().unwrap().to_owned(), convert(v))).collect(),
            other => panic!("unexpected snapshot scalar {other:?}"),
        }
    }
    convert(&yaml_rust2::YamlLoader::load_from_str(snapshot).unwrap()[0])
}
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
    let mut doc: serde_json::Value = tree(&saved);
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

#[test]
fn evaluated_values_preserve_local_sources_without_live_captures() {
    let mut vm = Vm::load(r#"{$call: [let, {f: {$call: [fn, [x], {$call: [add, {$ref: x}, 3]}]}}, {$call: [map, {$ref: f}, [7, 11]]}]}"#);
    vm.run();
    assert_eq!(vm.status(), Status::Finished);
    let evidence = vm.evaluated_values();
    assert_eq!(evidence["truncated"].as_bool(), Some(false));
    let rows = evidence["values"].as_vec().unwrap();
    assert!(rows.iter().any(|row| row["value"].as_i64() == Some(14)
        && row["location"]["path"].as_vec().is_some_and(|path| !path.is_empty())
        && row["line"].as_i64() == Some(1)));
    assert!(rows.iter().any(|row| row["runtime_values_omitted"].as_bool() == Some(true)));
    assert_eq!(Vm::restore(&vm.snapshot().unwrap()).unwrap().evaluated_values(), evidence);
    assert_eq!(Vm::load("42").evaluated_values()["values"].as_vec().unwrap().len(), 0);
}

#[test]
fn evaluated_evidence_is_bounded_and_rejects_forged_metadata() {
    let source = (0..500).map(|i| format!("value_{i}: {i}\n")).collect::<String>();
    let mut vm = Vm::load(&source);
    vm.run();
    assert_eq!(vm.status(), Status::Finished);
    assert_eq!(vm.evaluated_values()["truncated"].as_bool(), Some(true));
    let saved: serde_json::Value = tree(&vm.snapshot().unwrap());
    for (field, value) in [("evaluated_units", serde_json::json!(0)),
        ("evaluated", serde_json::json!([]))] {
        let mut changed = saved.clone();
        for row in changed["objects"].as_object_mut().unwrap().values_mut() {
            if row["kind"] == "evaluator" { row["value"][field] = value.clone(); }
        }
        assert!(Vm::restore(&changed.to_string()).is_err(), "accepted forged {field}");
    }
    assert_eq!(Vm::restore(&vm.snapshot().unwrap()).unwrap().evaluated_values(), vm.evaluated_values());
}

fn reversible_promise() -> yaml_rust2::Yaml {
    yaml_rust2::YamlLoader::load_from_str("{type: promise, name: durable, requires: [reversible]}").unwrap().remove(0)
}

#[test]
fn promised_execution_keeps_obligations_across_process_boundaries() {
    let mut machine = Vm::load("{$call: [add, 20, 22]}").with_promise(reversible_promise()).unwrap();
    assert!(helix::yaml_document(&machine.inspect()).contains("durable"));
    for _ in 0..100 {
        let saved = machine.snapshot().unwrap();
        assert!(saved.contains("durable"));
        machine = Vm::restore(&saved).unwrap();
        if machine.status() == Status::Finished { break; }
        machine = machine.step();
    }
    assert_eq!(machine.status(), Status::Finished);
    assert_eq!(machine.state()["root"].as_i64(), Some(42));
}

#[test]
fn promise_types_compose_and_reject_unregistered_behavior() {
    let mut machine = Vm::load(r#"
base: {$call: [promise, durable, [reversible]]}
child: {$call: [promise, child, [{$ref: base}]]}
machine: {$call: [mixin, {$call: [vm, {$call: [fn, [], 42]}]}, {$ref: child}]}
member: {$call: [is, {$ref: machine}, {$ref: base}]}
subtype: {$call: [subtype, {$ref: child}, {$ref: base}]}
plain: {$call: [is, {$call: [vm, {$call: [fn, [], 42]}]}, {$ref: base}]}
"#);
    machine.run();
    assert_eq!(machine.status(), Status::Finished, "{}", machine.state_string());
    assert_eq!(machine.state()["root"]["member"].as_bool(), Some(true));
    assert_eq!(machine.state()["root"]["subtype"].as_bool(), Some(true));
    assert_eq!(machine.state()["root"]["plain"].as_bool(), Some(false));
    let mut bad = Vm::load("{$call: [promise, dishonest, [unregistered]]}");
    bad.run();
    assert_eq!(bad.status(), Status::Error);
}

#[test]
fn promised_effects_are_refused_before_host_dispatch() {
    let mut machine = Vm::load("{$call: [effect, exit, 17]}").with_promise(reversible_promise()).unwrap();
    let mut host = helix::EffectHost::default();
    host.run(&mut machine);
    assert_eq!(machine.status(), Status::Error);
    assert_eq!(host.exit_code, 0);
    assert!(machine.pending_effect().is_some());
    assert!(Vm::restore(&machine.snapshot().unwrap()).is_ok());
}

#[test]
fn promise_refuses_oversized_external_state_without_losing_serializability() {
    let machine = Vm::load("42").with_promise(reversible_promise()).unwrap();
    let huge = yaml_rust2::Yaml::Array(vec![yaml_rust2::Yaml::String("x".repeat(1024 * 1024)); 40]);
    let refused = machine.signal(huge);
    assert_eq!(refused.status(), Status::Error);
    assert!(Vm::restore(&refused.snapshot().unwrap()).is_ok());
    assert_eq!(run(machine).state()["root"].as_i64(), Some(42));
}

#[test]
fn restored_promises_cannot_claim_unregistered_behavior() {
    let machine = Vm::load("42").with_promise(reversible_promise()).unwrap();
    let mut document: serde_json::Value = tree(&machine.snapshot().unwrap());
    let root = document["machine"].as_str().unwrap().to_owned();
    // Mutate through the ordinary value codec rather than relying on internal
    // pointer identities: promise strings must still undergo declaration validation.
    let encoded = document["objects"][&root]["value"]["promises"].to_string();
    document["objects"][&root]["value"]["promises"] = serde_json::from_str(&encoded.replace("reversible", "unregistered")).unwrap();
    assert!(Vm::restore(&document.to_string()).unwrap_err().contains("unsupported promise obligation"));
}

#[test]
fn promise_rolls_back_a_transition_that_exceeds_snapshot_depth() {
    // The source is shallow and admissible. Each iteration adds live collection
    // layers; runtime values permit more depth than the wire codec.
    let source = growing_state_program().to_string();
    let mut machine = Vm::load(&source).with_promise(reversible_promise()).unwrap();
    machine.run();
    assert_eq!(machine.status(), Status::Error);
    assert!(machine.state_string().contains("operation violates reversible state bounds"), "{}", machine.state_string());
    let restored = Vm::restore(&machine.snapshot().unwrap()).unwrap();
    assert_eq!(restored.state_string(), machine.state_string());
}

#[test]
fn promised_nested_child_preserves_parent_continuation() {
    let source = serde_json::json!({"$call": ["step",
        {"$call": ["mixin", {"$call": ["vm", {"$call": ["fn", [], {"$call": ["add", 20, 22]}]}]},
            {"$call": ["promise", "durable", ["reversible"]]}]}
    ]});
    let mut machine = Vm::load(&source.to_string()).with_promise(reversible_promise()).unwrap();
    for _ in 0..100 {
        let saved = machine.snapshot().unwrap();
        let restored = Vm::restore(&saved).unwrap();
        assert_eq!(restored.state_string(), machine.state_string());
        if machine.status() == Status::Finished { return; }
        machine = restored.step();
    }
    panic!("nested promised step did not finish");
}

fn growing_state_program() -> serde_json::Value {
    let mut body = serde_json::json!({"$ref": "acc"});
    for _ in 0..32 { body = serde_json::json!([body]); }
    serde_json::json!({"$call": ["until",
        {"$call": ["fn", ["acc"], false]},
        {"$call": ["fn", ["acc"], body]}, 0]})
}

fn effects_promise(operations: &[&str]) -> yaml_rust2::Yaml {
    yaml_rust2::YamlLoader::load_from_str(&serde_json::json!({
        "type": "promise", "name": "effects", "requires": [{"effects": operations}]
    }).to_string()).unwrap().remove(0)
}

#[test]
fn effect_promises_intersect_without_granting_authority() {
    let source = "{$call: [effect, time, null]}";
    let mut allowed = Vm::load(source).with_promise(effects_promise(&["time"])).unwrap();
    let mut host = helix::EffectHost::default();
    host.run(&mut allowed);
    assert_eq!(allowed.status(), helix::Status::Error);
    assert_ne!(allowed.state()["error"]["kind"].as_str(), Some("promise"));

    let mut allowed = Vm::load(source).with_promise(effects_promise(&["time"])).unwrap();
    host.allow_time = true;
    host.fixed_time = Some(123);
    host.run(&mut allowed);
    assert_eq!(allowed.status(), helix::Status::Finished);
    assert_eq!(allowed.state()["root"].as_i64(), Some(123));
    assert!(allowed.with_promise(effects_promise(&[])).is_err());
    let restored = Vm::restore(&allowed.snapshot().unwrap()).unwrap();
    assert_eq!(restored.state(), allowed.state());
    let mut damaged: serde_json::Value = tree(&allowed.snapshot().unwrap());
    let root = damaged["machine"].as_str().unwrap().to_owned();
    let promises = &mut damaged["objects"][&root]["value"]["promises"];
    *promises = serde_json::from_str(&promises.to_string().replace("\"time\"", "\"exit\"")).unwrap();
    assert!(Vm::restore(&damaged.to_string()).is_err());

    let mut denied = Vm::load("{$call: [effect, exit, 17]}")
        .with_promise(effects_promise(&["time", "exit"])).unwrap()
        .with_promise(effects_promise(&["time"])).unwrap();
    host.run(&mut denied);
    assert_eq!(denied.status(), helix::Status::Error);
    assert_eq!(host.exit_code, 0);
    assert_eq!(denied.state()["error"]["kind"].as_str(), Some("promise"));
}

#[test]
fn effect_promises_validate_pending_work_and_preserve_reversible_dispatch_refusal() {
    let mut pending = Vm::load("{$call: [effect, exit, 17]}");
    pending.run();
    assert!(pending.with_promise(effects_promise(&[])).is_err());
    assert!(pending.with_promise(effects_promise(&["exit"])).is_ok());
    let mut reversible = pending.with_promise(effects_promise(&["exit"])).unwrap()
        .with_promise(reversible_promise()).unwrap();
    let mut host = helix::EffectHost::default();
    host.run(&mut reversible);
    assert_eq!(reversible.status(), helix::Status::Error);
    assert_eq!(host.exit_code, 0);
    for requires in ["[{effects: null}]", "[{effects: [1]}]", "[{effects: ['']}]", "[{effects: [], extra: true}]"] {
        let descriptor = yaml_rust2::YamlLoader::load_from_str(&format!(
            "{{type: promise, name: invalid, requires: {requires}}}"
        )).unwrap().remove(0);
        assert!(Vm::load("42").with_promise(descriptor).is_err());
    }
}

#[test]
fn pending_server_startup_roundtrips_without_host_authority() {
    let mut machine = Vm::load("{$call: [http_serve, {bind: '127.0.0.1:0', requests: 1}, {$call: [fn, [request], null]}]}");
    machine.run();
    assert_eq!(machine.status(), Status::Signaled);
    let saved = machine.snapshot().unwrap();
    assert_eq!(Vm::restore(&saved).unwrap().state(), machine.state());
    let damaged = saved.replace("127.0.0.1:0", "0.0.0.0:0");
    assert!(Vm::restore(&damaged).is_err());
    let mut restored = Vm::restore(&saved).unwrap();
    helix::EffectHost::default().run(&mut restored);
    assert_eq!(restored.status(), Status::Error);
    assert_eq!(restored.state()["error"]["kind"].as_str(), Some("permission"));
}
