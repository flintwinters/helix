// Helix is a minimal, object-uniform systems language implemented in C++, with semantics inspired by Smalltalk. Every value is a cell, concretely represented as a map, and behavior emerges from the presence or absence of protocol slots rather than fixed types. There are only two fundamental operations: find and eval. Find performs name lookup with optional delegation through a parent link, while eval provides invocation semantics for any cell that defines it. Environments, closures, native functions, and even execution contexts are not distinct runtime categories but compositions of these capabilities. Errors are first-class cells, ensuring failure propagates uniformly without out-of-band control flow. The result is a highly regular system where objects can dynamically assume roles such as namespace, function, or evaluator without changing representation.

// Any cell can operate as a self-contained mini VM by implementing a `state` field that contains execution data such as stack, frames, and environment. Execution is therefore not centralized; instead, control flows by passing through cells that locally host and evolve their own state, effectively acting as composable, nested interpreters.

// To eliminate parser complexity and accelerate experimentation, the language uses YAML directly as its surface syntax. Programs are authored as YAML documents that map naturally onto the underlying cell structure, making code both data and executable form simultaneously. This approach enables rapid iteration on semantics without committing to a custom grammar, while preserving readability and structural clarity. Since YAML already encodes maps, sequences, and scalars, it aligns closely with the runtime model, allowing the interpreter to operate directly on parsed data structures. The combination of a Smalltalk-like message-passing core, C++-level control over memory and performance, and YAML-based syntax yields a compact but expressive experimental platform for declarative and reflective systems programming.

#include <iostream>
#include <memory>
#include <string>
#include <vector>
#include <unordered_map>
#include <algorithm>
#include <functional>
#include <cctype>
#include <fstream>

#include <c4/std/string.hpp>
#include <ryml.hpp>

using namespace std;

struct Cell;

using Ptr = shared_ptr<Cell>;

inline Ptr make_cell(auto* p) { return Ptr(p); }

static string pad(size_t depth) {
    return string(depth * 2, ' ');
}

static string to_string_copy(c4::csubstr s) {
    return string(s.str, s.len);
}

// Base object. Everything derives from this.
struct Cell {
    virtual Ptr find(const string& key) { return 0; }
    virtual Ptr eval(const vector<Ptr>& args) { return 0; }
    virtual string str(size_t depth = 0) const { return "<cell>"; }
    virtual ~Cell() = default;
};

// Error object. First class failure value.
struct Err : Cell {
    string msg;
    Err(string m) : msg(m) {}
    Ptr find(const string&) override { return make_cell(new Err(msg)); }
    Ptr eval(const vector<Ptr>&) override { return make_cell(new Err(msg)); }
    string str(size_t depth = 0) const override {
        return pad(depth) + "Err(" + msg + ")";
    }
};

// Integer primitive
struct Int : Cell {
    int v;
    Int(int x) : v(x) {}
    string str(size_t depth = 0) const override {
        return pad(depth) + to_string(v);
    }
};

// String primitive
struct Str : Cell {
    string v;
    Str(string s) : v(s) {}
    string str(size_t depth = 0) const override {
        return pad(depth) + "\"" + v + "\"";
    }
};

// Vector primitive
struct Vec : Cell {
    vector<Ptr> v;
    Vec(vector<Ptr> x) : v(move(x)) {}
    string str(size_t depth = 0) const override {
        if (v.empty()) return pad(depth) + "[]";

        string out = pad(depth) + "[\n";
        for (size_t i = 0; i < v.size(); ++i) {
            out += v[i]->str(depth + 1);
            if (i + 1 != v.size()) out += " ";
            out += "\n";
        }
        out += pad(depth) + "]";
        return out;
    }
};

// Map object. Core of environment, closures, vm host.
struct Map : Cell {
    unordered_map<string, Ptr> m;
    Map(unordered_map<string, Ptr> x) : m(move(x)) {}
    Ptr find(const string& key) override {
        if (m.count(key)) return m[key];
        if (m.count("parent")) return m["parent"]->find(key);
        return make_cell(new Err("missing: " + key));
    }
    Ptr eval(const vector<Ptr>& args) override {
        if (m.count("eval")) return m["eval"]->eval(args); // delegate to callable slot
        return make_cell(new Err("not callable"));
    }
    string str(size_t depth = 0) const override {
        if (m.empty()) return pad(depth) + "{}";

        vector<string> keys;
        keys.reserve(m.size());
        for (const auto& [key, _] : m) keys.push_back(key);
        sort(keys.begin(), keys.end());

        string out = pad(depth) + "{\n";
        for (size_t i = 0; i < keys.size(); ++i) {
            const auto& key = keys[i];
            out += pad(depth + 1) + key + ": ";
            auto value = m.at(key)->str(depth + 1);
            out += value.substr(pad(depth + 1).size());
            if (i + 1 != keys.size()) out += " ";
            out += "\n";
        }
        out += pad(depth) + "}";
        return out;
    }
};

// Native callable wrapper.
struct Fun : Cell {
    function<Ptr(const vector<Ptr>&)> fn;
    Fun(function<Ptr(const vector<Ptr>&)> f) : fn(move(f)) {}
    Ptr eval(const vector<Ptr>& args) override {
        return fn(args);
    }
    string str(size_t depth = 0) const override {
        return pad(depth) + "<fun>";
    }
};

static Ptr scalar_to_cell(c4::csubstr scalar) {
    const string text = to_string_copy(scalar);

    if (!text.empty()) {
        size_t start = 0;
        if (text[0] == '-' || text[0] == '+') {
            start = 1;
        }

        const bool is_integer =
            start < text.size() &&
            all_of(text.begin() + static_cast<ptrdiff_t>(start), text.end(), [](unsigned char ch) {
                return isdigit(ch) != 0;
            });

        if (is_integer) {
            return make_cell(new Int(stoi(text)));
        }
    }

    return make_cell(new Str(text));
}

static Ptr yaml_node_to_cell(ryml::ConstNodeRef node) {
    if (!node.valid()) {
        return make_cell(new Err("invalid yaml node"));
    }

    if (node.is_stream() || node.is_doc()) {
        if (!node.has_children()) {
            return make_cell(new Map({}));
        }
        return yaml_node_to_cell(node[0]);
    }

    if (node.is_map()) {
        unordered_map<string, Ptr> values;
        values.reserve(node.num_children());
        for (const auto child : node.children()) {
            values.emplace(to_string_copy(child.key()), yaml_node_to_cell(child));
        }
        return make_cell(new Map(move(values)));
    }

    if (node.is_seq()) {
        vector<Ptr> values;
        values.reserve(node.num_children());
        for (const auto child : node.children()) {
            values.push_back(yaml_node_to_cell(child));
        }
        return make_cell(new Vec(move(values)));
    }

    if (node.has_val()) {
        return scalar_to_cell(node.val());
    }

    return make_cell(new Map({}));
}

static Ptr yaml_string_to_cell(const string& yaml) {
    ryml::Tree tree = ryml::parse_in_arena(ryml::to_csubstr(yaml));
    return yaml_node_to_cell(tree.rootref());
}

static Ptr yaml_file_to_cell(const string& filename) {
    ifstream input(filename);
    if (!input) {
        return make_cell(new Err("unable to open file: " + filename));
    }

    const string yaml((istreambuf_iterator<char>(input)), istreambuf_iterator<char>());
    return yaml_string_to_cell(yaml);
}

int main(int argc, char** argv) {
    if (argc > 1) {
        cout << yaml_file_to_cell(argv[1])->str() << endl;
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
