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
