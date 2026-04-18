// Helix is a minimal, object-uniform systems language implemented in C++, with semantics inspired by Smalltalk. Every value is a cell, concretely represented as a map, and behavior emerges from the presence or absence of protocol slots rather than fixed types. There are only two fundamental operations: find and eval. Find performs name lookup with optional delegation through a parent link, while eval provides invocation semantics for any cell that defines it. Environments, closures, native functions, and even execution contexts are not distinct runtime categories but compositions of these capabilities. Errors are first-class cells, ensuring failure propagates uniformly without out-of-band control flow. The result is a highly regular system where objects can dynamically assume roles such as namespace, function, or evaluator without changing representation.

// Any cell can operate as a self-contained mini VM by implementing a `state` field that contains execution data such as stack, frames, and environment. Execution is therefore not centralized; instead, control flows by passing through cells that locally host and evolve their own state, effectively acting as composable, nested interpreters.

// To eliminate parser complexity and accelerate experimentation, the language uses YAML directly as its surface syntax. Programs are authored as YAML documents that map naturally onto the underlying cell structure, making code both data and executable form simultaneously. This approach enables rapid iteration on semantics without committing to a custom grammar, while preserving readability and structural clarity. Since YAML already encodes maps, sequences, and scalars, it aligns closely with the runtime model, allowing the interpreter to operate directly on parsed data structures. The combination of a Smalltalk-like message-passing core, C++-level control over memory and performance, and YAML-based syntax yields a compact but expressive experimental platform for declarative and reflective systems programming.

#include <iostream>
#include <cstdio>
#include <unordered_map>
#include <utility>
#include <vector>

#include "core.hpp"
#include "utils.hpp"
#include "helix.hpp"
#include "ryml_interface.hpp"

using namespace std;

Ptr make_builtin_env();

static void emit_yaml_doc(const Ptr& cell, FILE* out) {
    ryml::Tree tree = cell_to_ryml_tree(cell);
    ryml::emit_yaml(tree, out);
}

static Ptr load_root_from_yaml(const string& filename) {
    Ptr root = yaml_file_to_cell(
        filename,
        [](string message) { return error(move(message)); },
        [](int value) { return make_cell(new Int(value)); },
        [](string value) { return make_cell(new Str(move(value))); },
        [](vector<Ptr> values) { return make_cell(new Vec(move(values))); },
        [](unordered_map<string, Ptr> values) { return make_cell(new Map(move(values))); }
    );

    shared_ptr<Map> root_map = dynamic_pointer_cast<Map>(root);
    if (!root_map) {
        return error("program root must be a map");
    }

    root_map->m["parent"] = make_builtin_env();
    return root;
}

static Ptr run_yaml_file(const string& filename, Ptr& result_out) {
    Ptr root = load_root_from_yaml(filename);
    result_out = root->find(root, "eval")->eval(root);
    return root;
}

static Ptr run_demo() {
    Ptr env = make_builtin_env();

    Ptr call = make_cell(new Vec({
        make_cell(new Str("add")),
        make_cell(new Int(2)),
        make_cell(new Int(3))
    }));

    Ptr result = call->eval(env);
    Ptr missing = env->find(env, "missing");
    Ptr missing_find = missing->find(env, "still_missing");
    Ptr missing_eval = missing->eval(env);
    Ptr non_callable = env->eval(env);

    cout << dynamic_pointer_cast<Int>(result)->v << endl;
    cout << missing->str() << endl;
    cout << missing_find->str() << endl;
    cout << missing_eval->str() << endl;
    cout << non_callable->str() << endl;
    return env;
}

int main(int argc, char** argv) {
    Ptr vm;
    if (argc > 1) {
        Ptr result;
        vm = run_yaml_file(argv[1], result);
        cout << result->str() << endl;
        emit_yaml_doc(vm, stderr);
    } else {
        vm = run_demo();
        emit_yaml_doc(vm, stderr);
    }

    return 0;
}
