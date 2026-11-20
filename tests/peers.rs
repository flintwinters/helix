//! Coordinated promised participants, exercised by manage.py test --suite library.
use helix::{PromisedSystem, Status, Vm};
use serde_json::{Value as J, json};
use yaml_rust2::{Yaml, YamlLoader};

const CLIENT: &str = include_str!("programs/peer_client.yaml");
const SERVER: &str = include_str!("programs/peer_server.yaml");
const ENDLESS: &str = "!call [until, !call [fn, [n], false], !call [fn, [n], !call [add, !ref n, 1]], 0]";
fn promised(source: &str) -> Vm {
    let promise = YamlLoader::load_from_str("{type: promise, name: reversible, requires: [reversible]}").unwrap().remove(0);
    Vm::load(source).with_promise(promise).unwrap()
}
fn system() -> PromisedSystem {
    PromisedSystem::new([("client".into(), promised(CLIENT)), ("server".into(), promised(SERVER))].into()).unwrap()
}
fn finish(mut state: PromisedSystem) -> PromisedSystem {
    for _ in 0..200 {
        if state.finished() { return state; }
        state = state.advance().unwrap().expect("unexpected blocked system");
    }
    panic!("communication did not finish")
}
#[test]
fn every_joint_boundary_restores_and_accepted_requests_do_not_repeat() {
    let original = system();
    let expected = finish(original.clone()).state();
    assert_eq!(expected["machines"]["client"]["root"], Yaml::Integer(8));
    assert_eq!(expected["delivery_count"], Yaml::Integer(2));
    let mut state = original.clone();
    let mut observed_accepted = false;
    for _ in 0..200 {
        let saved = state.snapshot().unwrap();
        let restored = PromisedSystem::restore(&saved).unwrap();
        assert_eq!(restored.snapshot().unwrap(), saved);
        if state.delivery_count() == 1 {
            observed_accepted = true;
            let duplicate = restored.deliver("client", 1, "server", 1).unwrap();
            assert_eq!(duplicate.snapshot().unwrap(), saved);
            assert!(restored.deliver("client", 1, "server", 2).is_err());
            assert_eq!(finish(restored).state(), expected);
        }
        if state.finished() { break; }
        state = state.advance().unwrap().unwrap();
    }
    assert!(observed_accepted);
    assert_eq!(state.state(), expected);
    assert_eq!(original.delivery_count(), 0);
    assert_eq!(original.machine("client").unwrap().status(), Status::Ready);
    assert_eq!(finish(original).state(), expected);
    assert_eq!(state.deliver("client", 1, "server", 1).unwrap().state(), expected);
}
#[test]
fn joint_history_cannot_be_omitted_or_spliced() {
    let initial: J = serde_json::from_str(&system().snapshot().unwrap()).unwrap();
    let completed = finish(system());
    let document: J = serde_json::from_str(&completed.snapshot().unwrap()).unwrap();
    let mut missing = document.clone();
    missing["deliveries"] = json!([]);
    assert!(PromisedSystem::restore(&missing.to_string()).is_err());
    let mut splice = document.clone();
    splice["machines"]["client"] = initial["machines"]["client"].clone();
    assert!(PromisedSystem::restore(&splice.to_string()).is_err());
    let machines = ["client", "server"].map(|name| (name.into(), completed.machine(name).unwrap().clone())).into();
    assert!(PromisedSystem::new(machines).is_err());
    let mut mismatch = document.clone();
    let server = mismatch["machines"]["server"].to_string().replace("\"integer\":7", "\"float\":7.0");
    assert_ne!(server, mismatch["machines"]["server"].to_string());
    mismatch["machines"]["server"] = serde_json::from_str(&server).unwrap();
    assert!(PromisedSystem::restore(&mismatch.to_string()).is_err());
}
#[test]
fn unpromised_participants_and_external_operations_have_no_peer_authority() {
    assert!(PromisedSystem::new([("plain".into(), Vm::load("42"))].into()).is_err());
    let mut state = PromisedSystem::new([("client".into(), promised("!call [effect, exit, 17]"))].into()).unwrap();
    for _ in 0..30 {
        match state.advance().unwrap() { Some(next) => state = next, None => break }
    }
    assert!(!state.finished());
    assert_eq!(state.machine("client").unwrap().pending_effect().unwrap().operation, "exit");
    assert_eq!(state.delivery_count(), 0);
    assert!(state.deliver("client", 1, "absent", 1).is_err());
}

#[test]
fn runnable_participants_share_progress_and_cursor_restores() {
    let mut state = PromisedSystem::new([("a_busy".into(), promised(ENDLESS)), ("z_finite".into(), promised("42"))].into()).unwrap();
    for _ in 0..20 {
        state = PromisedSystem::restore(&state.snapshot().unwrap()).unwrap();
        state = state.advance().unwrap().unwrap();
        if state.machine("z_finite").unwrap().status() == Status::Finished { return; }
    }
    panic!("finite participant starved behind runnable peer");
}

#[test]
fn communication_is_not_starved_by_a_runnable_participant() {
    let mut state = PromisedSystem::new([("a_busy".into(), promised(ENDLESS)), ("client".into(), promised(CLIENT)), ("server".into(), promised(SERVER))].into()).unwrap();
    for _ in 0..200 {
        state = state.advance().unwrap().unwrap();
        if state.delivery_count() == 2 { return; }
    }
    panic!("communication starved behind runnable peer");
}

#[test]
fn delivery_rejection_preserves_both_pending_requests() {
    let state = waiting_system();
    let mut document: J = serde_json::from_str(&state.snapshot().unwrap()).unwrap();
    for object in document["machines"]["server"]["objects"].as_object_mut().unwrap().values_mut() {
        if object["kind"] == "evaluator" { object["value"]["remaining_values"] = json!(0); }
    }
    let exhausted = PromisedSystem::restore(&document.to_string()).unwrap();
    let before = exhausted.snapshot().unwrap();
    assert!(exhausted.deliver("client", 1, "server", 1).is_err());
    assert_eq!(exhausted.snapshot().unwrap(), before);
    assert_eq!(exhausted.delivery_count(), 0);
    assert_eq!(exhausted.machine("client").unwrap().pending_effect().unwrap().id, 1);
    assert_eq!(exhausted.machine("server").unwrap().pending_effect().unwrap().id, 1);
}

#[test]
fn interrupted_receiver_restores_before_communication_resumes() {
    let state = waiting_system();
    assert_eq!(state.state()["status"].as_str(), Some("running"));
    let interrupted = state.signal("server", Yaml::String("pause".into())).unwrap();
    assert_eq!(interrupted.state()["status"].as_str(), Some("blocked"));
    assert!(interrupted.deliver("client", 1, "server", 1).is_err());
    let restored = PromisedSystem::restore(&interrupted.snapshot().unwrap()).unwrap();
    assert!(restored.machine("server").unwrap().is_interrupted());
    assert_eq!(restored.machine("server").unwrap().pending_effect().unwrap().id, 1);
    let completed = finish(restored.resume_signal("server").unwrap());
    assert_eq!(completed.state()["machines"]["client"]["root"], Yaml::Integer(8));
    assert!(interrupted.machine("server").unwrap().is_interrupted());
}

fn waiting_system() -> PromisedSystem {
    let mut state = system();
    for _ in 0..100 {
        if ["client", "server"].iter().all(|name| state.machine(name).unwrap().pending_effect().is_some()) { return state; }
        // Stop before the scheduler commits the waiting rendezvous.
        for name in ["client", "server"] {
            if matches!(state.machine(name).unwrap().status(), Status::Ready | Status::Running) {
                state = state.step(name).unwrap();
            }
        }
    }
    panic!("participants never reached rendezvous");
}

#[test]
fn server_retains_accumulated_state_across_two_exchanges() {
    let client = r#"!call [get, [
        !call [effect, peer_send, {to: server, value: 7}],
        !call [effect, peer_receive, {from: server}],
        !call [effect, peer_send, {to: server, value: 5}],
        !call [effect, peer_receive, {from: server}]
    ], 3]"#;
    let server = r#"!call [fold, !call [fn, [total, item],
        !call [!call [fn, [next],
            !call [get, [!call [effect, peer_send, {to: client, value: !ref next}], !ref next], 1]],
            !call [add, !ref total, !call [effect, peer_receive, {from: client}]]]
    ], 0, [null, null]]"#;
    let mut state = PromisedSystem::new([
        ("client".into(), promised(client)), ("server".into(), promised(server)),
    ].into()).unwrap();
    let mut restored_between_exchanges = false;
    for _ in 0..400 {
        if !restored_between_exchanges && state.delivery_count() == 2 {
            state = PromisedSystem::restore(&state.snapshot().unwrap()).unwrap();
            restored_between_exchanges = true;
        }
        if state.finished() { break; }
        state = state.advance().unwrap().unwrap_or_else(|| panic!("stateful exchange blocked: {}", helix::yaml_document(&state.state())));
    }
    assert!(restored_between_exchanges);
    assert!(state.finished());
    assert_eq!(state.delivery_count(), 4);
    assert_eq!(state.machine("client").unwrap().state()["effects"][1]["outcome"], Yaml::Integer(7));
    assert_eq!(state.machine("client").unwrap().state()["root"], Yaml::Integer(12));
    assert_eq!(state.machine("server").unwrap().state()["root"], Yaml::Integer(12));
}
