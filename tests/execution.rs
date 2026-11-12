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
        "[add, 20, 22]",
        "[if, true, [add, 40, 2], [div, 1, 0]]",
        "[cond, false, 0, true, 42, [div, 1, 0]]",
        "[and, true, false, [div, 1, 0]]",
        "[call, [call, [fn, [x], [fn, [y], [add, x, y]]], 20], 22]",
        "f: [fn, [n], [if, [eq, n, 0], 1, [mul, n, [f, [sub, n, 1]]]]]\nanswer: [f, 6]",
        "[map, [fn, [n], [mul, n, n]], [list, 1, 2, 3]]",
        "[filter, [fn, [n], [gt, n, 1]], [list, 1, 2, 3]]",
        "[fold, [fn, [a, b], [add, a, b]], 0, [list, 1, 2, 3]]",
        "[each, [fn, [n], n], [list, 1, 2]]",
        "[until, [fn, [n], [ge, n, 4]], [fn, [n], [add, n, 1]], 0]",
        "positive: [refine, integer, [fn, [n], [gt, n, 0]]]\nf: [fn, [[n, positive]], [add, n, 1], positive]\nanswer: [f, 41]",
        "t: [refine, integer, [fn, [n], [gt, n, 0]]]\nanswer: [violations, [list, 1, -1, 2], [list_of, t]]",
        "[try, [finally, [div, 1, 0], [raise, \"cleanup\", \"replacement\"]], [fn, [e], [get, e, \"kind\"]]]",
        "[finally, [div, 1, 0], 42]",
        "make: [fn, [t], [fn, [[n, t]], n, t]]\nf: [make, integer]\nanswer: [f, 42]",
        "t: [refine, integer, [fn, [n], [gt, n, 0]]]\nbase: [class, \"base\", [record, \"n\", [default, t, 1]]]\nchild: [inherit, \"child\", [list, base], {n: 2}]",
        "t: [refine, integer, [fn, [n], [gt, n, 0]]]\nanswer: [subtype, [enum, 1, 2], t]",
        "[call, [fn, [], 42]]",
        "[type_time, [finally, [type_time, [add, 20, 22]], null]]",
        "[try, [type_time, [div, 1, 0]], [fn, [e], [get, e, \"phase\"]]]",
        "c: [constrain, [class, \"c\", [record, \"x\", [default, integer, 42]]], [fn, [record], [gt, [get, record, \"x\"], 0]]]\nr: [type_time, [realize, c]]",
        "[raise, \"payload\", \"function\", [fn, [], 42]]",
        "[try, [raise, \"payload\", \"function\", [fn, [], 42]], [fn, [e], [call, [get, e, \"data\"]]]]",
        "[try, [raise, \"payload\", \"machine\", [vm, [fn, [], 42]]], [fn, [e], [get, [inspect, [get, e, \"data\"]], \"status\"]]]",
        "[[fn, [n], [add, n, 2]], 40]",
        "[try, [div, 1, 0], [fn, [e], 42]]",
        "[try, [finally, 1, [div, 1, 0]], [fn, [e], 42]]",
        "[let, {x: [add, x, 1]}, [try, x, [fn, [e], [get, e, \"kind\"]]]]",
    ] {
        equivalent(source);
    }
}
#[test]
fn intermediate_calls_iterations_predicates_and_unwinding_are_visible() {
    let source = "t: [refine, integer, [fn, [n], [gt, n, 0]]]\nf: [fn, [[n, t]], [finally, [expect, n, t], null]]\na: [map, f, [list, 1, 2]]";
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
    let source = r#"
seed: 40
child: [vm, [fn, [], [add, seed, 2]]]
advanced: [step, child]
old: [inspect, child]
new: [inspect, advanced]
done: [fn, [m], [eq, [get, [inspect, m], "status"], "finished"]]
advance: [fn, [m], [step, m]]
left: [until, done, advance, child]
right: [until, done, advance, advanced]
left_view: [inspect, left]
right_view: [inspect, right]
same: [eq, left, right]
unchanged: [eq, child, [vm, [fn, [], [add, seed, 2]]]]
"#;
    let vm = equivalent(source);
    assert_eq!(vm.status(), Status::Finished, "{}", vm.state_string());
    let r = root(&vm);
    assert_eq!(r["old"]["status"].as_str(), Some("ready"));
    assert_eq!(r["new"]["status"].as_str(), Some("running"));
    assert_eq!(r["left_view"]["outcome"].as_i64(), Some(42));
    assert_eq!(r["right_view"]["outcome"].as_i64(), Some(42));
    assert_eq!(r["same"].as_bool(), Some(true));
    let error = finish_by_steps(Vm::load("[inspect, [vm, [fn, [], [div, 1, 0]]]]"));
    assert_eq!(root(&error)["status"].as_str(), Some("ready"));
}
#[test]
fn nested_child_dispatch_and_lexical_capture() {
    let source = r#"
finish: [fn, [m], [until, [fn, [v], [or, [eq, [get, [inspect, v], "status"], "finished"], [eq, [get, [inspect, v], "status"], "error"]]], [fn, [v], [step, v]], m]]
make: [fn, [n], [vm, [fn, [], [add, n, 2]]]]
outer: [vm, [fn, [], [get, [inspect, [finish, [make, 40]]], "outcome"]]]
answer: [get, [inspect, [finish, outer]], "outcome"]
"#;
    let vm = equivalent(source);
    assert_eq!(vm.status(), Status::Finished, "{}", vm.state_string());
    assert_eq!(root(&vm)["answer"].as_i64(), Some(42));
}
#[test]
fn exact_numeric_comparison_and_function_data_separation() {
    let vm = equivalent(
        r#"
a: [eq, 9007199254740993, 9007199254740992.0]
b: [gt, 9007199254740993, 9007199254740992.0]
c: [lt, 9223372036854775807, 9223372036854775808.0]
d: [eq, -9223372036854775808, -9223372036854775808.0]
e: [lt, -1, -0.5]
f: [gt, 0, -0.5]
g: [eq, [list, "fn", [list], 42], [fn, [], 42]]
h: [type, [fn, [], 42]]
i: [type, [list, "fn", [list], 42]]
j: [eq, [fn, [n], [add, n, 1]], [fn, [n], [add, n, 1]]]
"#,
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
    let mut vm = Vm::load("\"\": {\"a/b\": [add, 20, 22]}\n\"0\": [list, 42]");
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
            .any(|p| p == &vec![Yaml::String("0".into()), Yaml::Integer(1)])
    );
}

#[test]
fn recursive_binding_equality_and_function_environments() {
    let source = r#"
a:
  offset: 1
  f: [fn, [n], [if, [eq, n, 0], offset, [g, [sub, n, 1]]]]
  g: [fn, [n], [f, n]]
b:
  offset: 1
  f: [fn, [n], [if, [eq, n, 0], offset, [g, [sub, n, 1]]]]
  g: [fn, [n], [f, n]]
c:
  offset: 2
  f: [fn, [n], [if, [eq, n, 0], offset, [g, [sub, n, 1]]]]
  g: [fn, [n], [f, n]]
same: [eq, a.f, b.f]
different: [ne, a.f, c.f]
make: [fn, [n], [fn, [], n]]
captures_same: [eq, [make, 1], [make, 1]]
captures_different: [ne, [make, 1], [make, 2]]
factory: [fn, [], 42]
child: [vm, factory]
child_type: [is, child, vm]
child_equal: [eq, child, [vm, factory]]
advanced_equal: [eq, [step, child], [step, [vm, factory]]]
code_scalar_kinds: [ne, [fn, [], 1], [fn, [], 1.0]]
code_symbol_is_not_map: [ne, [fn, [], integer], [fn, [], {symbol: "integer"}]]
code_map_order: [ne, [fn, [], {a: 1, b: 2}], [fn, [], {b: 2, a: 1}]]
"#;
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
        "first: [fn, [], [div, 1, 0]]\nsecond: [fn, [], [div, 1, 0]]\nequal: [eq, first, second]",
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
    let mut vm = Vm::load("bad: [div, 1, 0]\ntyped: [type_time, bad]\nother: [add, missing, 1]");
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
    for body in ["[fn, [], [get, row, 1]]", "[vm, [fn, [], [get, row, 1]]]"] {
        let source = format!(
            "[until, [fn, [row], [eq, [get, row, 0], 3000]], [fn, [row], [list, [add, [get, row, 0], 1], {body}]], [list, 0, null]]"
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
    let mut vm = Vm::load("f: [fn, [x], [add, x, [effect, \"time\", null]]]\nanswer: [f, 2]");
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
    let mut vm = Vm::load("[effect, \"time\", null]");
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
    let ready = Vm::load("[add, 2, 3]");
    let mut interrupted = ready.signal(Yaml::String("pause".into()));
    interrupted.run();
    assert_eq!(interrupted.status(), Status::Signaled);
    let mut continued = interrupted.resume_signal();
    continued.run();
    assert_eq!(continued.state()["root"].as_i64(), Some(5));
}

#[test]
fn effect_failure_unwinds_cleanup_and_disables_unsafe_recovery() {
    let mut vm = Vm::load("[finally, [effect, \"file_read\", \"missing\"], [effect, \"stdout\", \"cleanup\"]]");
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
    let mut vm = Vm::load("[type_time, [effect, \"time\", null]]");
    vm.run();
    assert_eq!(vm.state()["error"]["kind"].as_str(), Some("purity"));
    assert_eq!(vm.state()["phase"].as_str(), Some("type"));
    assert!(vm.pending_effect().is_none());
    let mut checked = Vm::load("a: [effect, \"time\", null]\nb: [effect, \"random\", null]");
    checked.check();
    assert_eq!(checked.status(), Status::Error);
    assert_eq!(checked.state()["errors"].as_vec().unwrap().len(), 2);
}

#[test]
fn invalid_host_data_is_a_structured_failure() {
    for value in [Yaml::BadValue, Yaml::Real("NaN".into()), Yaml::Alias(1)] {
        let mut vm = Vm::load("[effect, \"time\", null]");
        vm.run();
        let mut resolved = vm.resolve_effect(Ok(value));
        resolved.run();
        assert_eq!(resolved.status(), Status::Error);
        assert_eq!(resolved.state()["error"]["kind"].as_str(), Some("type"));
    }
    let mut pending = Vm::load("[effect, \"time\", null]");
    pending.run();
    let failed = pending.signal(Yaml::BadValue);
    assert_eq!(failed.status(), Status::Error);
    assert_eq!(failed.resolve_effect(Ok(Yaml::Integer(1))).state(), failed.state());
}

#[test]
fn nested_machine_steps_inherit_effect_phase_restrictions() {
    let source = r#"
- type_time
- - let
  - original: [vm, [fn, [], [effect, "time", null]]]
    stopped: [fn, [m], [eq, [get, [inspect, m], "status"], "error"]]
    advance: [fn, [m], [step, m]]
  - [get, [inspect, [until, stopped, advance, original]], "outcome"]
"#;
    let mut vm = Vm::load(source);
    vm.run();
    assert_eq!(vm.state()["root"]["kind"].as_str(), Some("purity"));
    assert_eq!(vm.state()["root"]["phase"].as_str(), Some("type"));
}

#[test]
fn child_type_time_persists_across_separate_parent_steps() {
    let source = r#"
- let
- original: [vm, [fn, [], [type_time, [effect, "time", null]]]]
  stopped: [fn, [m], [eq, [get, [inspect, m], "status"], "error"]]
  advance: [fn, [m], [step, m]]
- [get, [inspect, [until, stopped, advance, original]], "outcome"]
"#;
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
    let mut vm = Vm::load("[effect, \"random\", null]");
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
    let mut invalid = Vm::load("[effect, \"random\", null]");
    invalid.run();
    invalid = invalid.signal(Yaml::BadValue);
    host.run(&mut invalid);
    assert_eq!(invalid.status(), Status::Error);
    assert_eq!(host.random_seed, seed_after);
}
