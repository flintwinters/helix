// Helix is a minimal, object-uniform systems language implemented in C++, with semantics inspired by Smalltalk. Every value is a cell, concretely represented as a map, and behavior emerges from the presence or absence of protocol slots rather than fixed types. There are only two fundamental operations: find and eval. Find performs name lookup with optional delegation through a parent link, while eval provides invocation semantics for any cell that defines it. Environments, closures, native functions, and even execution contexts are not distinct runtime categories but compositions of these capabilities. Errors are first-class cells, ensuring failure propagates uniformly without out-of-band control flow. The result is a highly regular system where objects can dynamically assume roles such as namespace, function, or evaluator without changing representation.

// Any cell can operate as a self-contained mini VM by implementing a `state` field that contains execution data such as stack, frames, and environment. Execution is therefore not centralized; instead, control flows by passing through cells that locally host and evolve their own state, effectively acting as composable, nested interpreters.

// To eliminate parser complexity and accelerate experimentation, the language uses YAML directly as its surface syntax. Programs are authored as YAML documents that map naturally onto the underlying cell structure, making code both data and executable form simultaneously. This approach enables rapid iteration on semantics without committing to a custom grammar, while preserving readability and structural clarity. Since YAML already encodes maps, sequences, and scalars, it aligns closely with the runtime model, allowing the interpreter to operate directly on parsed data structures. The combination of a Smalltalk-like message-passing core, C++-level control over memory and performance, and YAML-based syntax yields a compact but expressive experimental platform for declarative and reflective systems programming.

#include <iostream>
#include <string>
#include <vector>
#include <unordered_map>
#include <functional>

#include "core.cpp" // NOLINT(bugprone-suspicious-include)

using namespace std;

using MakeErr = function<Ptr(string)>;
using MakeInt = function<Ptr(int)>;
using MakeStr = function<Ptr(string)>;
using MakeVec = function<Ptr(vector<Ptr>)>;
using MakeMap = function<Ptr(unordered_map<string, Ptr>)>;

Ptr yaml_file_to_cell(
    const string& filename,
    const MakeErr& make_err,
    const MakeInt& make_int,
    const MakeStr& make_str,
    const MakeVec& make_vec,
    const MakeMap& make_map
);

int main(int argc, char** argv) {
    if (argc > 1) {
        auto parsed = yaml_file_to_cell(
            argv[1],
            [](string message) { return error(move(message)); },
            [](int value) { return make_cell(new Int(value)); },
            [](string value) { return make_cell(new Str(move(value))); },
            [](vector<Ptr> values) { return make_cell(new Vec(move(values))); },
            [](unordered_map<string, Ptr> values) { return make_cell(new Map(move(values))); }
        );
        cout << parsed->str() << endl;
        return 0;
    }

    auto add = make_cell(new Fun([](const vector<Ptr>& a) {
        auto x = dynamic_pointer_cast<Int>(a[0])->v;
        auto y = dynamic_pointer_cast<Int>(a[1])->v;
        return make_cell(new Int(x+y));
    }));

    auto env = make_cell(new Map({
        {"add", add}
    }));

    auto result = env->find("add")->eval({ make_cell(new Int(2)), make_cell(new Int(3)) });
    auto missing = env->find("missing");
    auto missing_find = missing->find("still_missing");
    auto missing_eval = missing->eval({});
    auto non_callable = env->eval({});

    cout << dynamic_pointer_cast<Int>(result)->v << endl;
    cout << missing->str() << endl;
    cout << missing_find->str() << endl;
    cout << missing_eval->str() << endl;
    cout << non_callable->str() << endl;
}
