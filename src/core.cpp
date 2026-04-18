#include <algorithm>
#include <functional>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

using namespace std;

struct Cell;
struct Err;

using Ptr = shared_ptr<Cell>;

inline Ptr make_cell(auto* p) { return Ptr(p); }

static string pad(size_t depth) {
    return string(depth * 2, ' ');
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
    Err(string m) : msg(move(m)) {}
    string str(size_t depth = 0) const override {
        return pad(depth) + "Err(" + msg + ")";
    }
};

inline Ptr error(string s) {
    return make_cell(new Err(move(s)));
}

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
    Str(string s) : v(move(s)) {}
    string str(size_t depth = 0) const override {
        return pad(depth) + v;
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

// Map object. Core of environment, closures, vm host.
struct Map : Cell {
    unordered_map<string, Ptr> m;
    Map(unordered_map<string, Ptr> x) : m(move(x)) {}
    Ptr find(const string& key) override {
        if (m.count(key)) return m[key];
        if (m.count("parent")) return m["parent"]->find(key);
        return error("missing: " + key);
    }
    Ptr eval(const vector<Ptr>& args) override {
        if (m.count("eval")) return m["eval"]->eval(args);
        return error("not callable");
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
