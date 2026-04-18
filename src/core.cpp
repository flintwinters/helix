#include "core.hpp"

#include <algorithm>
#include <utility>

using namespace std;

string pad(size_t depth) {
    return string(depth * 2, ' ');
}

Ptr Cell::find(const string&) {
    return nullptr;
}

Ptr Cell::eval(const vector<Ptr>&) {
    return nullptr;
}

string Cell::str(size_t) const {
    return "<cell>";
}

Err::Err(string m) : msg(move(m)) {}

Ptr Err::find(const string&) {
    return error(msg);
}

Ptr Err::eval(const vector<Ptr>&) {
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

string Str::str(size_t depth) const {
    return pad(depth) + v;
}

Vec::Vec(vector<Ptr> x) : v(move(x)) {}

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

Fun::Fun(function<Ptr(const vector<Ptr>&)> f) : fn(move(f)) {}

Ptr Fun::eval(const vector<Ptr>& args) {
    return fn(args);
}

string Fun::str(size_t depth) const {
    return pad(depth) + "<fun>";
}

Map::Map(unordered_map<string, Ptr> x) : m(move(x)) {}

Ptr Map::find(const string& key) {
    if (m.count(key)) return m[key];
    if (m.count("parent")) return m["parent"]->find(key);
    return error("missing: " + key);
}

Ptr Map::eval(const vector<Ptr>& args) {
    if (m.count("eval")) return m["eval"]->eval(args);
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
