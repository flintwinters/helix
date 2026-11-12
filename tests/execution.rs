//! Immutable execution contracts. Invoked only through manage.py's execution/library suites.
use helix::{Status, Vm};
use yaml_rust2::Yaml;

fn active(vm: &Vm) -> bool {
    matches!(vm.status(), Status::Ready | Status::Running)
}
fn root(vm: &Vm) -> Yaml {
    vm.state()["root"].clone()
}
fn finish_by_steps(mut vm: Vm) -> Vm {
    for _ in 0..100_000 {
        if !active(&vm) {
            return vm;
        }
        vm = vm.step();
    }
    panic!("bounded execution did not finish")
}
fn equivalent(source: &str) -> Vm {
    let ready = Vm::load(source);
    let initial = ready.inspect();
    assert_eq!(initial["limits"]["max_depth"].as_i64(), Some(200));
    assert_eq!(initial["limits"]["max_iterations"].as_i64(), Some(100_000));
    let mut uninterrupted = ready.clone();
    uninterrupted.run();
    let mut stepped = ready.clone();
    let mut retained = Vec::new();
    let mut seed = 714209_u64;
    for index in 0..100_000 {
        if !active(&stepped) {
            break;
        }
        // Fixed-seed reservoir sampling covers pauses throughout execution, including late
        // child calls and unwinding, while keeping the number of retained versions bounded.
        if index % 137 == 0 {
            seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1);
            let candidate = if retained.len() < 12 {
                retained.len()
            } else {
                (seed as usize) % (index / 137 + 1)
            };
            if candidate < 12 {
                let saved = (stepped.clone(), stepped.inspect());
                if candidate == retained.len() {
                    retained.push(saved)
                } else {
                    retained[candidate] = saved;
                }
            }
        }
        let next = stepped.step();
        stepped = next;
    }
    assert!(!active(&stepped));
    assert_eq!(stepped, uninterrupted);
    assert_eq!(ready.inspect(), initial);
    for (saved, inspection) in retained {
        assert_eq!(saved.inspect(), inspection, "a retained state changed");
        let mut resumed = saved;
        resumed.run();
        assert_eq!(resumed, uninterrupted);
    }
    assert_eq!(stepped.step(), stepped);
    stepped
}
#[test]
fn execution_paths_pause_and_resume() {
    for source in [
        r##"{"$call": ["add", 20, 22]}"##,
        r##"{"$call": ["if", true, {"$call": ["add", 40, 2]}, {"$call": ["div", 1, 0]}]}"##,
        r##"{"$call": ["cond", false, 0, true, 42, {"$call": ["div", 1, 0]}]}"##,
        r##"{"$call": ["and", true, false, {"$call": ["div", 1, 0]}]}"##,
        r##"{"$call": ["call", {"$call": ["call", {"$call": ["fn", ["x"], {"$call": ["fn", ["y"], {"$call": ["add", {"$ref": "x"}, {"$ref": "y"}]}]}]}, 20]}, 22]}"##,
        r##"{"f": {"$call": ["fn", ["n"], {"$call": ["if", {"$call": ["eq", {"$ref": "n"}, 0]}, 1, {"$call": ["mul", {"$ref": "n"}, {"$call": ["f", {"$call": ["sub", {"$ref": "n"}, 1]}]}]}]}]}, "answer": {"$call": ["f", 6]}}"##,
        r##"{"$call": ["map", {"$call": ["fn", ["n"], {"$call": ["mul", {"$ref": "n"}, {"$ref": "n"}]}]}, {"$call": ["list", 1, 2, 3]}]}"##,
        r##"{"$call": ["filter", {"$call": ["fn", ["n"], {"$call": ["gt", {"$ref": "n"}, 1]}]}, {"$call": ["list", 1, 2, 3]}]}"##,
        r##"{"$call": ["fold", {"$call": ["fn", ["a", "b"], {"$call": ["add", {"$ref": "a"}, {"$ref": "b"}]}]}, 0, {"$call": ["list", 1, 2, 3]}]}"##,
        r##"{"$call": ["each", {"$call": ["fn", ["n"], {"$ref": "n"}]}, {"$call": ["list", 1, 2]}]}"##,
        r##"{"$call": ["until", {"$call": ["fn", ["n"], {"$call": ["ge", {"$ref": "n"}, 4]}]}, {"$call": ["fn", ["n"], {"$call": ["add", {"$ref": "n"}, 1]}]}, 0]}"##,
        r##"{"positive": {"$call": ["refine", {"$ref": "integer"}, {"$call": ["fn", ["n"], {"$call": ["gt", {"$ref": "n"}, 0]}]}]}, "f": {"$call": ["fn", [["n", {"$ref": "positive"}]], {"$call": ["add", {"$ref": "n"}, 1]}, {"$ref": "positive"}]}, "answer": {"$call": ["f", 41]}}"##,
        r##"{"t": {"$call": ["refine", {"$ref": "integer"}, {"$call": ["fn", ["n"], {"$call": ["gt", {"$ref": "n"}, 0]}]}]}, "answer": {"$call": ["violations", {"$call": ["list", 1, -1, 2]}, {"$call": ["list_of", {"$ref": "t"}]}]}}"##,
        r##"{"$call": ["try", {"$call": ["finally", {"$call": ["div", 1, 0]}, {"$call": ["raise", "cleanup", "replacement"]}]}, {"$call": ["fn", ["e"], {"$call": ["get", {"$ref": "e"}, "kind"]}]}]}"##,
        r##"{"$call": ["finally", {"$call": ["div", 1, 0]}, 42]}"##,
        r##"{"make": {"$call": ["fn", ["t"], {"$call": ["fn", [["n", {"$ref": "t"}]], {"$ref": "n"}, {"$ref": "t"}]}]}, "f": {"$call": ["make", {"$ref": "integer"}]}, "answer": {"$call": ["f", 42]}}"##,
        r##"{"t": {"$call": ["refine", {"$ref": "integer"}, {"$call": ["fn", ["n"], {"$call": ["gt", {"$ref": "n"}, 0]}]}]}, "base": {"$call": ["class", "base", {"$call": ["record", "n", {"$call": ["default", {"$ref": "t"}, 1]}]}]}, "child": {"$call": ["inherit", "child", {"$call": ["list", {"$ref": "base"}]}, {"n": 2}]}}"##,
        r##"{"t": {"$call": ["refine", {"$ref": "integer"}, {"$call": ["fn", ["n"], {"$call": ["gt", {"$ref": "n"}, 0]}]}]}, "answer": {"$call": ["subtype", {"$call": ["enum", 1, 2]}, {"$ref": "t"}]}}"##,
        r##"{"$call": ["call", {"$call": ["fn", [], 42]}]}"##,
        r##"{"$call": ["type_time", {"$call": ["finally", {"$call": ["type_time", {"$call": ["add", 20, 22]}]}, null]}]}"##,
        r##"{"$call": ["try", {"$call": ["type_time", {"$call": ["div", 1, 0]}]}, {"$call": ["fn", ["e"], {"$call": ["get", {"$ref": "e"}, "phase"]}]}]}"##,
        r##"{"c": {"$call": ["constrain", {"$call": ["class", "c", {"$call": ["record", "x", {"$call": ["default", {"$ref": "integer"}, 42]}]}]}, {"$call": ["fn", ["record"], {"$call": ["gt", {"$call": ["get", {"$ref": "record"}, "x"]}, 0]}]}]}, "r": {"$call": ["type_time", {"$call": ["realize", {"$ref": "c"}]}]}}"##,
        r##"{"$call": ["raise", "payload", "function", {"$call": ["fn", [], 42]}]}"##,
        r##"{"$call": ["try", {"$call": ["raise", "payload", "function", {"$call": ["fn", [], 42]}]}, {"$call": ["fn", ["e"], {"$call": ["call", {"$call": ["get", {"$ref": "e"}, "data"]}]}]}]}"##,
        r##"{"$call": ["try", {"$call": ["raise", "payload", "machine", {"$call": ["vm", {"$call": ["fn", [], 42]}]}]}, {"$call": ["fn", ["e"], {"$call": ["get", {"$call": ["inspect", {"$call": ["get", {"$ref": "e"}, "data"]}]}, "status"]}]}]}"##,
        r##"{"$call": [{"$call": ["fn", ["n"], {"$call": ["add", {"$ref": "n"}, 2]}]}, 40]}"##,
        r##"{"$call": ["try", {"$call": ["div", 1, 0]}, {"$call": ["fn", ["e"], 42]}]}"##,
        r##"{"$call": ["try", {"$call": ["finally", 1, {"$call": ["div", 1, 0]}]}, {"$call": ["fn", ["e"], 42]}]}"##,
        r##"{"$call": ["let", {"x": {"$call": ["add", {"$ref": "x"}, 1]}}, {"$call": ["try", {"$ref": "x"}, {"$call": ["fn", ["e"], {"$call": ["get", {"$ref": "e"}, "kind"]}]}]}]}"##,
    ] {
        equivalent(source);
    }
}
#[test]
fn intermediate_calls_iterations_predicates_and_unwinding_are_visible() {
    let source = r##"{"t": {"$call": ["refine", {"$ref": "integer"}, {"$call": ["fn", ["n"], {"$call": ["gt", {"$ref": "n"}, 0]}]}]}, "f": {"$call": ["fn", [["n", {"$ref": "t"}]], {"$call": ["finally", {"$call": ["expect", {"$ref": "n"}, {"$ref": "t"}]}, null]}]}, "a": {"$call": ["map", {"$ref": "f"}, {"$call": ["list", 1, 2]}]}}"##;
    let mut vm = Vm::load(source);
    let mut observed = std::collections::HashSet::new();
    while active(&vm) {
        let view = vm.inspect();
        assert_eq!(view["status"].as_str(), Some(vm.status().as_str()));
        for request in view["continuations"].as_vec().unwrap() {
            observed.insert(request["kind"].as_str().unwrap().to_string());
        }
        vm = vm.step();
    }
    assert_eq!(vm.status(), Status::Finished);
    for kind in [
        "return_call",
        "iterate",
        "predicate_return",
        "parameter_type",
        "cleanup",
    ] {
        assert!(observed.contains(kind), "missing intermediate {kind}");
    }
}
#[test]
fn child_creation_is_lazy_and_versions_branch_independently() {
    let source = r##"{"seed": 40, "child": {"$call": ["vm", {"$call": ["fn", [], {"$call": ["add", {"$ref": "seed"}, 2]}]}]}, "advanced": {"$call": ["step", {"$ref": "child"}]}, "old": {"$call": ["inspect", {"$ref": "child"}]}, "new": {"$call": ["inspect", {"$ref": "advanced"}]}, "done": {"$call": ["fn", ["m"], {"$call": ["eq", {"$call": ["get", {"$call": ["inspect", {"$ref": "m"}]}, "status"]}, "finished"]}]}, "advance": {"$call": ["fn", ["m"], {"$call": ["step", {"$ref": "m"}]}]}, "left": {"$call": ["until", {"$ref": "done"}, {"$ref": "advance"}, {"$ref": "child"}]}, "right": {"$call": ["until", {"$ref": "done"}, {"$ref": "advance"}, {"$ref": "advanced"}]}, "left_view": {"$call": ["inspect", {"$ref": "left"}]}, "right_view": {"$call": ["inspect", {"$ref": "right"}]}, "same": {"$call": ["eq", {"$ref": "left"}, {"$ref": "right"}]}, "unchanged": {"$call": ["eq", {"$ref": "child"}, {"$call": ["vm", {"$call": ["fn", [], {"$call": ["add", {"$ref": "seed"}, 2]}]}]}]}}"##;
    let vm = equivalent(source);
    assert_eq!(vm.status(), Status::Finished, "{}", vm.state_string());
    let r = root(&vm);
    assert_eq!(r["old"]["status"].as_str(), Some("ready"));
    assert_eq!(r["new"]["status"].as_str(), Some("running"));
    assert_eq!(r["left_view"]["outcome"].as_i64(), Some(42));
    assert_eq!(r["right_view"]["outcome"].as_i64(), Some(42));
    assert_eq!(r["same"].as_bool(), Some(true));
    let error = finish_by_steps(Vm::load(r##"{"$call": ["inspect", {"$call": ["vm", {"$call": ["fn", [], {"$call": ["div", 1, 0]}]}]}]}"##));
    assert_eq!(root(&error)["status"].as_str(), Some("ready"));
}
#[test]
fn nested_child_dispatch_and_lexical_capture() {
    let source = r##"{"finish": {"$call": ["fn", ["m"], {"$call": ["until", {"$call": ["fn", ["v"], {"$call": ["or", {"$call": ["eq", {"$call": ["get", {"$call": ["inspect", {"$ref": "v"}]}, "status"]}, "finished"]}, {"$call": ["eq", {"$call": ["get", {"$call": ["inspect", {"$ref": "v"}]}, "status"]}, "error"]}]}]}, {"$call": ["fn", ["v"], {"$call": ["step", {"$ref": "v"}]}]}, {"$ref": "m"}]}]}, "make": {"$call": ["fn", ["n"], {"$call": ["vm", {"$call": ["fn", [], {"$call": ["add", {"$ref": "n"}, 2]}]}]}]}, "outer": {"$call": ["vm", {"$call": ["fn", [], {"$call": ["get", {"$call": ["inspect", {"$call": ["finish", {"$call": ["make", 40]}]}]}, "outcome"]}]}]}, "answer": {"$call": ["get", {"$call": ["inspect", {"$call": ["finish", {"$ref": "outer"}]}]}, "outcome"]}}"##;
    let vm = equivalent(source);
    assert_eq!(vm.status(), Status::Finished, "{}", vm.state_string());
    assert_eq!(root(&vm)["answer"].as_i64(), Some(42));
}
#[test]
fn exact_numeric_comparison_and_function_data_separation() {
    let vm = equivalent(
        r##"{"a": {"$call": ["eq", 9007199254740993, 9007199254740992.0]}, "b": {"$call": ["gt", 9007199254740993, 9007199254740992.0]}, "c": {"$call": ["lt", 9223372036854775807, 9.223372036854776e+18]}, "d": {"$call": ["eq", -9223372036854775808, -9.223372036854776e+18]}, "e": {"$call": ["lt", -1, -0.5]}, "f": {"$call": ["gt", 0, -0.5]}, "g": {"$call": ["eq", {"$call": ["list", "fn", {"$call": ["list"]}, 42]}, {"$call": ["fn", [], 42]}]}, "h": {"$call": ["type", {"$call": ["fn", [], 42]}]}, "i": {"$call": ["type", {"$call": ["list", "fn", {"$call": ["list"]}, 42]}]}, "j": {"$call": ["eq", {"$call": ["fn", ["n"], {"$call": ["add", {"$ref": "n"}, 1]}]}, {"$call": ["fn", ["n"], {"$call": ["add", {"$ref": "n"}, 1]}]}]}}"##,
    );
    let r = root(&vm);
    for (key, expected) in [
        ("a", false),
        ("b", true),
        ("c", true),
        ("d", true),
        ("e", true),
        ("f", true),
        ("g", false),
        ("j", true),
    ] {
        assert_eq!(r[key].as_bool(), Some(expected), "{key}");
    }
    assert_eq!(r["h"].as_str(), Some("function"));
    assert_eq!(r["i"].as_str(), Some("list"));
}
#[test]
fn typed_paths_preserve_unusual_keys() {
    let mut vm = Vm::load(r##"{"": {"a/b": {"$call": ["add", 20, 22]}}, "0": {"$call": ["list", 42]}}"##);
    let mut locations = Vec::new();
    while active(&vm) {
        let view = vm.inspect();
        for r in view["continuations"].as_vec().unwrap() {
            if let Some(path) = r["location"]["path"].as_vec() {
                locations.push(path.clone());
            }
        }
        vm = vm.step();
    }
    assert!(locations.iter().any(|p| p.is_empty()));
    assert!(
        locations
            .iter()
            .any(|p| p == &vec![Yaml::String("".into()), Yaml::String("a/b".into())])
    );
    assert!(
        locations
            .iter()
            .any(|p| p == &vec![Yaml::String("0".into()), Yaml::String("$call".into()), Yaml::Integer(1)])
    );
}

#[test]
fn recursive_binding_equality_and_function_environments() {
    let source = r##"{"a": {"offset": 1, "f": {"$call": ["fn", ["n"], {"$call": ["if", {"$call": ["eq", {"$ref": "n"}, 0]}, {"$ref": "offset"}, {"$call": ["g", {"$call": ["sub", {"$ref": "n"}, 1]}]}]}]}, "g": {"$call": ["fn", ["n"], {"$call": ["f", {"$ref": "n"}]}]}}, "b": {"offset": 1, "f": {"$call": ["fn", ["n"], {"$call": ["if", {"$call": ["eq", {"$ref": "n"}, 0]}, {"$ref": "offset"}, {"$call": ["g", {"$call": ["sub", {"$ref": "n"}, 1]}]}]}]}, "g": {"$call": ["fn", ["n"], {"$call": ["f", {"$ref": "n"}]}]}}, "c": {"offset": 2, "f": {"$call": ["fn", ["n"], {"$call": ["if", {"$call": ["eq", {"$ref": "n"}, 0]}, {"$ref": "offset"}, {"$call": ["g", {"$call": ["sub", {"$ref": "n"}, 1]}]}]}]}, "g": {"$call": ["fn", ["n"], {"$call": ["f", {"$ref": "n"}]}]}}, "same": {"$call": ["eq", {"$ref": "a.f"}, {"$ref": "b.f"}]}, "different": {"$call": ["ne", {"$ref": "a.f"}, {"$ref": "c.f"}]}, "make": {"$call": ["fn", ["n"], {"$call": ["fn", [], {"$ref": "n"}]}]}, "captures_same": {"$call": ["eq", {"$call": ["make", 1]}, {"$call": ["make", 1]}]}, "captures_different": {"$call": ["ne", {"$call": ["make", 1]}, {"$call": ["make", 2]}]}, "factory": {"$call": ["fn", [], 42]}, "child": {"$call": ["vm", {"$ref": "factory"}]}, "child_type": {"$call": ["is", {"$ref": "child"}, {"$ref": "vm"}]}, "child_equal": {"$call": ["eq", {"$ref": "child"}, {"$call": ["vm", {"$ref": "factory"}]}]}, "advanced_equal": {"$call": ["eq", {"$call": ["step", {"$ref": "child"}]}, {"$call": ["step", {"$call": ["vm", {"$ref": "factory"}]}]}]}, "code_scalar_kinds": {"$call": ["ne", {"$call": ["fn", [], 1]}, {"$call": ["fn", [], 1.0]}]}, "code_symbol_is_not_map": {"$call": ["ne", {"$call": ["fn", [], {"$ref": "integer"}]}, {"$call": ["fn", [], {"symbol": "integer"}]}]}, "code_map_order": {"$call": ["eq", {"$call": ["fn", [], {"a": 1, "b": 2}]}, {"$call": ["fn", [], {"b": 2, "a": 1}]}]}}"##;
    let vm = equivalent(source);
    assert_eq!(vm.status(), Status::Finished, "{}", vm.state_string());
    let value = root(&vm);
    for key in [
        "same",
        "different",
        "captures_same",
        "captures_different",
        "child_type",
        "child_equal",
        "advanced_equal",
        "code_scalar_kinds",
        "code_symbol_is_not_map",
        "code_map_order",
    ] {
        assert_eq!(value[key].as_bool(), Some(true), "{key}");
    }
    let view = vm.state_string();
    assert!(
        view.len() < 100_000,
        "recursive environments expanded indefinitely"
    );
}

#[test]
fn inspection_keeps_distinct_source_locations_for_equal_functions() {
    let mut vm = Vm::load(
        r##"{"first": {"$call": ["fn", [], {"$call": ["div", 1, 0]}]}, "second": {"$call": ["fn", [], {"$call": ["div", 1, 0]}]}, "equal": {"$call": ["eq", {"$ref": "first"}, {"$ref": "second"}]}}"##,
    );
    vm.run();
    let state = vm.state();
    assert_eq!(state["root"]["equal"].as_bool(), Some(true));
    let first = state["root"]["first"]["ref"].as_str().unwrap();
    let second = state["root"]["second"]["ref"].as_str().unwrap();
    assert_ne!(first, second);
    assert_eq!(
        state["objects"][first]["location"]["path"][0].as_str(),
        Some("first")
    );
    assert_eq!(
        state["objects"][second]["location"]["path"][0].as_str(),
        Some("second")
    );
}

#[test]
fn check_mode_keeps_independent_runtime_and_type_time_failures() {
    let mut vm = Vm::load(r##"{"bad": {"$call": ["div", 1, 0]}, "typed": {"$call": ["type_time", {"$ref": "bad"}]}, "other": {"$call": ["add", {"$ref": "missing"}, 1]}}"##);
    vm.check();
    let state = vm.state();
    assert_eq!(state["mode"].as_str(), Some("check"));
    assert!(state["phase"].is_badvalue());
    let errors = state["errors"].as_vec().unwrap();
    assert_eq!(errors.len(), 3);
    assert_eq!(
        errors
            .iter()
            .filter(|e| e["phase"].as_str() == Some("type"))
            .count(),
        1
    );
}

#[test]
fn retained_capture_graphs_drop_on_small_stacks_and_thread_teardown() {
    for body in [r##"{"$call": ["fn", [], {"$call": ["get", {"$ref": "row"}, 1]}]}"##, r##"{"$call": ["vm", {"$call": ["fn", [], {"$call": ["get", {"$ref": "row"}, 1]}]}]}"##] {
        let source = format!(
            "{{$call: [until, {{$call: [fn, [row], {{$call: [eq, {{$call: [get, {{$ref: row}}, 0]}}, 3000]}}]}}, {{$call: [fn, [row], {{$call: [list, {{$call: [add, {{$call: [get, {{$ref: row}}, 0]}}, 1]}}, {body}]}}]}}, [0, null]]}}"
        );
        std::thread::Builder::new()
            .stack_size(512 * 1024)
            .spawn(move || {
                thread_local! {
                    static RETAINED: std::cell::RefCell<Option<Vm>> = const { std::cell::RefCell::new(None) };
                }
                let ready = Vm::load(&source);
                let original = ready.inspect();
                let mut completed = ready.clone();
                completed.run();
                assert_eq!(completed.status(), Status::Finished);
                let retained = completed.clone();
                drop(completed);
                assert_eq!(ready.inspect(), original);
                RETAINED.with(|slot| *slot.borrow_mut() = Some(retained));
                // The last deep graph is released by TLS after this closure returns.
            })
            .unwrap()
            .join()
            .unwrap();
    }
}

#[test]
fn effects_yield_preserve_continuations_and_do_not_repeat_on_resume() {
    let mut vm = Vm::load(r##"{"f": {"$call": ["fn", ["x"], {"$call": ["add", {"$ref": "x"}, {"$call": ["effect", "time", null]}]}]}, "answer": {"$call": ["f", 2]}}"##);
    vm.run();
    assert_eq!(vm.status(), Status::Signaled);
    let request: &helix::EffectRequest = vm.pending_effect().unwrap();
    assert_eq!(request.operation, "time");
    let retained = vm.state();
    assert_eq!(vm.step().state(), retained);
    let mut resolved = vm.resolve_effect(Ok(Yaml::Integer(40)));
    resolved.run();
    assert_eq!(resolved.state()["root"]["answer"].as_i64(), Some(42));
    assert_eq!(resolved.state()["effects"].as_vec().unwrap().len(), 1);
    resolved.run();
    assert!(resolved.pending_effect().is_none());
    assert_eq!(vm.state(), retained);
}

#[test]
fn external_signal_preserves_pending_effect_and_requires_explicit_resume() {
    let mut vm = Vm::load(r##"{"$call": ["effect", "time", null]}"##);
    vm.run();
    let interrupted = vm.signal(Yaml::String("interrupt".into()));
    assert_eq!(interrupted.resume_signal().status(), Status::Signaled);
    let mut resolved = interrupted.resolve_effect(Ok(Yaml::Integer(7)));
    resolved.run();
    assert_eq!(resolved.status(), Status::Signaled);
    assert_eq!(resolved.state()["signal"].as_str(), Some("interrupt"));
    let mut resumed = resolved.resume_signal();
    resumed.run();
    assert_eq!(resumed.state()["root"].as_i64(), Some(7));
    let ready = Vm::load(r##"{"$call": ["add", 2, 3]}"##);
    let mut interrupted = ready.signal(Yaml::String("pause".into()));
    interrupted.run();
    assert_eq!(interrupted.status(), Status::Signaled);
    let mut continued = interrupted.resume_signal();
    continued.run();
    assert_eq!(continued.state()["root"].as_i64(), Some(5));
}

#[test]
fn effect_failure_unwinds_cleanup_and_disables_unsafe_recovery() {
    let mut vm = Vm::load(r##"{"$call": ["finally", {"$call": ["effect", "file_read", "missing"]}, {"$call": ["effect", "stdout", "cleanup"]}]}"##);
    vm.run();
    let mut failed = vm.resolve_effect(Err(("io".into(), "missing file".into())));
    failed.run();
    assert_eq!(failed.pending_effect().unwrap().operation, "stdout");
    assert_eq!(failed.pending_effect().unwrap().id, 2);
    let mut done = failed.resolve_effect(Ok(Yaml::Null));
    done.run();
    assert_eq!(done.state()["error"]["kind"].as_str(), Some("io"));
    assert_eq!(done.state()["effects"].as_vec().unwrap().len(), 2);
    assert!(!done.recovery_state_string().contains("recovery:"));
}

#[test]
fn effects_are_rejected_during_type_time_and_checking() {
    let mut vm = Vm::load(r##"{"$call": ["type_time", {"$call": ["effect", "time", null]}]}"##);
    vm.run();
    assert_eq!(vm.state()["error"]["kind"].as_str(), Some("purity"));
    assert_eq!(vm.state()["phase"].as_str(), Some("type"));
    assert!(vm.pending_effect().is_none());
    let mut checked = Vm::load(r##"{"a": {"$call": ["effect", "time", null]}, "b": {"$call": ["effect", "random", null]}}"##);
    checked.check();
    assert_eq!(checked.status(), Status::Error);
    assert_eq!(checked.state()["errors"].as_vec().unwrap().len(), 2);
}

#[test]
fn invalid_host_data_is_a_structured_failure() {
    for value in [Yaml::BadValue, Yaml::Real("NaN".into()), Yaml::Alias(1)] {
        let mut vm = Vm::load(r##"{"$call": ["effect", "time", null]}"##);
        vm.run();
        let mut resolved = vm.resolve_effect(Ok(value));
        resolved.run();
        assert_eq!(resolved.status(), Status::Error);
        assert_eq!(resolved.state()["error"]["kind"].as_str(), Some("type"));
    }
    let mut pending = Vm::load(r##"{"$call": ["effect", "time", null]}"##);
    pending.run();
    let failed = pending.signal(Yaml::BadValue);
    assert_eq!(failed.status(), Status::Error);
    assert_eq!(failed.resolve_effect(Ok(Yaml::Integer(1))).state(), failed.state());
}

#[test]
fn nested_machine_steps_inherit_effect_phase_restrictions() {
    let source = r##"{"$call": ["type_time", {"$call": ["let", {"original": {"$call": ["vm", {"$call": ["fn", [], {"$call": ["effect", "time", null]}]}]}, "stopped": {"$call": ["fn", ["m"], {"$call": ["eq", {"$call": ["get", {"$call": ["inspect", {"$ref": "m"}]}, "status"]}, "error"]}]}, "advance": {"$call": ["fn", ["m"], {"$call": ["step", {"$ref": "m"}]}]}}, {"$call": ["get", {"$call": ["inspect", {"$call": ["until", {"$ref": "stopped"}, {"$ref": "advance"}, {"$ref": "original"}]}]}, "outcome"]}]}]}"##;
    let mut vm = Vm::load(source);
    vm.run();
    assert_eq!(vm.state()["root"]["kind"].as_str(), Some("purity"));
    assert_eq!(vm.state()["root"]["phase"].as_str(), Some("type"));
}

#[test]
fn child_type_time_persists_across_separate_parent_steps() {
    let source = r##"{"$call": ["let", {"original": {"$call": ["vm", {"$call": ["fn", [], {"$call": ["type_time", {"$call": ["effect", "time", null]}]}]}]}, "stopped": {"$call": ["fn", ["m"], {"$call": ["eq", {"$call": ["get", {"$call": ["inspect", {"$ref": "m"}]}, "status"]}, "error"]}]}, "advance": {"$call": ["fn", ["m"], {"$call": ["step", {"$ref": "m"}]}]}}, {"$call": ["get", {"$call": ["inspect", {"$call": ["until", {"$ref": "stopped"}, {"$ref": "advance"}, {"$ref": "original"}]}]}, "outcome"]}]}"##;
    let mut vm = Vm::load(source);
    vm.run();
    assert_eq!(vm.state()["root"]["kind"].as_str(), Some("purity"));
    assert_eq!(vm.state()["root"]["phase"].as_str(), Some("type"));
}

#[test]
fn signal_data_obeys_graph_node_limits_without_counting_map_keys() {
    let ready = Vm::load("null");
    let at_limit = ready.signal(Yaml::Array(vec![Yaml::Null; 99_999]));
    assert_eq!(at_limit.status(), Status::Signaled);
    let oversized = ready.signal(Yaml::Array(vec![Yaml::Null; 100_000]));
    assert_eq!(oversized.status(), Status::Error);
    assert_eq!(oversized.state()["error"]["kind"].as_str(), Some("type"));
    assert_eq!(oversized.state()["error"]["message"].as_str(), Some("effect data exceeds runtime limits"));
    let fields = (0..50_000).map(|index| (Yaml::String(index.to_string()), Yaml::Null)).collect();
    assert_eq!(ready.signal(Yaml::Hash(fields)).status(), Status::Signaled);
}

#[test]
fn host_respects_interruptions_and_never_repeats_completed_effects() {
    use helix::EffectHost;
    let mut host = EffectHost { allow_random: true, random_seed: Some(7), ..Default::default() };
    let mut vm = Vm::load(r##"{"$call": ["effect", "random", null]}"##);
    vm.run();
    vm = vm.signal(Yaml::String("pause".into()));
    let interrupted = vm.state();
    host.run(&mut vm);
    assert_eq!(host.random_seed, Some(7));
    assert_eq!(vm.state(), interrupted);
    assert!(vm.pending_effect().is_some());
    vm = vm.resume_signal();
    host.run(&mut vm);
    assert_eq!(vm.status(), Status::Finished);
    let seed_after = host.random_seed;
    assert_ne!(seed_after, Some(7));
    let completed = vm.state();
    host.run(&mut vm);
    assert_eq!(host.random_seed, seed_after);
    assert_eq!(vm.state(), completed);
    let mut invalid = Vm::load(r##"{"$call": ["effect", "random", null]}"##);
    invalid.run();
    invalid = invalid.signal(Yaml::BadValue);
    host.run(&mut invalid);
    assert_eq!(invalid.status(), Status::Error);
    assert_eq!(host.random_seed, seed_after);
}
