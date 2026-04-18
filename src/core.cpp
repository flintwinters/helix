#include "core.hpp"

#include <algorithm>
#include <utility>

using namespace std;

static shared_ptr<Map> ensure_state_map(const Ptr& vm) {
    auto vm_map = dynamic_pointer_cast<Map>(vm);
    if (!vm_map) {
        return nullptr;
    }

    auto existing = vm_map->m.find("state");
    if (existing != vm_map->m.end()) {
        auto state = dynamic_pointer_cast<Map>(existing->second);
        if (state) {
            return state;
        }
    }

    auto state = make_cell(new Map({
        {"args", make_cell(new Vec({}))}
    }));
    vm_map->m["state"] = state;
    return dynamic_pointer_cast<Map>(state);
}

static Ptr eval_with_args(const Ptr& vm, const Ptr& actor, vector<Ptr> args) {
    auto state = ensure_state_map(vm);
    if (!state) {
        return error("vm has no mutable state");
    }

    auto previous = state->m.find("args");
    const bool had_previous = previous != state->m.end();
    Ptr previous_args = had_previous ? previous->second : Ptr{};

    state->m["args"] = make_cell(new Vec(move(args)));
    auto result = actor->eval(vm);

    if (had_previous) {
        state->m["args"] = previous_args;
    } else {
        state->m.erase("args");
    }

    return result;
}

string pad(size_t depth) {
    return string(depth * 2, ' ');
}

vector<Ptr> current_args(const Ptr& vm) {
    auto state = dynamic_pointer_cast<Map>(vm->find(vm, "state"));
    if (!state) {
        return {};
    }

    auto args = state->m.find("args");
    if (args == state->m.end()) {
        return {};
    }

    auto values = dynamic_pointer_cast<Vec>(args->second);
    if (!values) {
        return {};
    }

    return values->v;
}

Ptr Cell::find(const Ptr&, const string& key) {
    return error("missing: " + key);
}

Ptr Cell::eval(const Ptr&) {
    return error("not callable");
}

string Cell::str(size_t) const {
    return "<cell>";
}


Err::Err(string m) : msg(move(m)) {}
Ptr Err::find(const Ptr&, const string&) {
    return error(msg);
}
Ptr Err::eval(const Ptr&) {
    return error(msg);
}
string Err::str(size_t depth) const {
    return pad(depth) + "Err(" + msg + ")";
}


Ptr error(string s) {
    return make_cell(new Err(move(s)));
}


Int::Int(int x) : v(x) {}
string Int::str(size_t depth) const {
    return pad(depth) + to_string(v);
}


Str::Str(string s) : v(move(s)) {}
Ptr Str::eval(const Ptr& vm) {
    return vm->find(vm, v);
}
string Str::str(size_t depth) const {
    return pad(depth) + v;
}


Vec::Vec(vector<Ptr> x) : v(move(x)) {}
Ptr Vec::eval(const Ptr& vm) {
    if (v.empty()) {
        return error("cannot eval empty vector");
    }

    auto actor = v[0]->eval(vm);
    vector<Ptr> args;
    args.reserve(v.size() - 1);
    for (size_t i = 1; i < v.size(); ++i) {
        args.push_back(v[i]->eval(vm));
    }
    return eval_with_args(vm, actor, move(args));
}
string Vec::str(size_t depth) const {
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


Fun::Fun(function<Ptr(const Ptr&)> f) : fn(move(f)) {}
Ptr Fun::eval(const Ptr& vm) {
    return fn(vm);
}
string Fun::str(size_t depth) const {
    return pad(depth) + "<fun>";
}


Map::Map(unordered_map<string, Ptr> x) : m(move(x)) {}
Ptr Map::find(const Ptr&, const string& key) {
    if (m.count(key)) return m[key];
    if (m.count("parent")) return m["parent"]->find(m["parent"], key);
    return error("missing: " + key);
}
Ptr Map::eval(const Ptr& vm) {
    if (m.count("eval")) return m["eval"]->eval(vm);
    if (m.count("parent")) {
        auto actor = m["parent"]->find(m["parent"], "eval");
        return actor->eval(vm);
    }
    return error("not callable");
}
string Map::str(size_t depth) const {
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
