#pragma once

#include <cstddef>
#include <functional>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

struct Cell;
struct Err;
struct Int;
struct Str;
struct Vec;
struct Fun;
struct Map;

using Ptr = std::shared_ptr<Cell>;

template <class T>
inline Ptr make_cell(T* p) {
    return Ptr(p);
}

std::string pad(std::size_t depth);
Ptr error(std::string s);

struct Cell {
    virtual Ptr find(const std::string& key);
    virtual Ptr eval(const std::vector<Ptr>& args);
    virtual std::string str(std::size_t depth = 0) const;
    virtual ~Cell() = default;
};

struct Err : Cell {
    std::string msg;

    explicit Err(std::string m);
    Ptr find(const std::string& key) override;
    Ptr eval(const std::vector<Ptr>& args) override;
    std::string str(std::size_t depth = 0) const override;
};

struct Int : Cell {
    int v;

    explicit Int(int x);
    std::string str(std::size_t depth = 0) const override;
};

struct Str : Cell {
    std::string v;

    explicit Str(std::string s);
    std::string str(std::size_t depth = 0) const override;
};

struct Vec : Cell {
    std::vector<Ptr> v;

    explicit Vec(std::vector<Ptr> x);
    std::string str(std::size_t depth = 0) const override;
};

struct Fun : Cell {
    std::function<Ptr(const std::vector<Ptr>&)> fn;

    explicit Fun(std::function<Ptr(const std::vector<Ptr>&)> f);
    Ptr eval(const std::vector<Ptr>& args) override;
    std::string str(std::size_t depth = 0) const override;
};

struct Map : Cell {
    std::unordered_map<std::string, Ptr> m;

    explicit Map(std::unordered_map<std::string, Ptr> x);
    Ptr find(const std::string& key) override;
    Ptr eval(const std::vector<Ptr>& args) override;
    std::string str(std::size_t depth = 0) const override;
};
