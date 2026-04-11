#pragma once

#include <iosfwd>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

class Cell;
using Func = Cell& (*)(Cell&);
using namespace std;

extern Cell& Zygote;

Cell& Error(const char* s);
void clear_rooted_errors();
string load_file(const string& path);

class Cell {
public:
    enum Type {
        INT, STR, FUN, VEC, MAP, ANY, ERROR
    };

    bool alive = true;
    vector<Cell*> parents;

    Cell() = default;
    Cell(const Cell&) = delete;
    Cell& operator=(const Cell&) = delete;
    virtual ~Cell() = default;

    operator bool() const;

    Cell& operator()(Cell& c);
    Cell& operator[](Cell& c);
    Cell& operator[](int i_);
    Cell& operator[](const char* s_);
    Cell& operator[](string s_);

    virtual Type type() const = 0;
    virtual Cell& call(Cell& c);
    virtual Cell& index(Cell& c);
    virtual Cell& index(int i_);
    virtual Cell& index(const string& s_);
    virtual string to_string() const = 0;
    virtual bool is_truthy() const = 0;
    virtual int as_int() const;
    virtual const string* str_value() const;
    virtual vector<Cell*>* vec_value();
    virtual const vector<Cell*>* vec_value() const;
    virtual unordered_map<string, Cell*>* map_value();
    virtual const unordered_map<string, Cell*>* map_value() const;
    virtual void bind(const string& key, Cell* c);
    virtual void push(Cell* c);
    virtual void clear();
    Cell& own_result(Cell* c);
    operator string();

private:
    unique_ptr<Cell> result;
};

class IntCell final : public Cell {
public:
    explicit IntCell(int value_);

    Type type() const override;
    Cell& call(Cell& c) override;
    Cell& index(int i_) override;
    Cell& index(const string& s_) override;
    string to_string() const override;
    bool is_truthy() const override;
    int as_int() const override;

private:
    int value;
};

class StringCell final : public Cell {
public:
    explicit StringCell(const char* value_);
    explicit StringCell(string value_);

    Type type() const override;
    Cell& call(Cell& c) override;
    Cell& index(int i_) override;
    Cell& index(const string& s_) override;
    string to_string() const override;
    bool is_truthy() const override;
    const string* str_value() const override;

private:
    string value;
};

class FunCell final : public Cell {
public:
    explicit FunCell(Func value_);

    Type type() const override;
    Cell& call(Cell& c) override;
    Cell& index(int i_) override;
    Cell& index(const string& s_) override;
    string to_string() const override;
    bool is_truthy() const override;

private:
    Func value;
};

class VecCell final : public Cell {
public:
    VecCell() = default;

    Type type() const override;
    Cell& call(Cell& c) override;
    Cell& index(int i_) override;
    Cell& index(const string& s_) override;
    string to_string() const override;
    bool is_truthy() const override;
    vector<Cell*>* vec_value() override;
    const vector<Cell*>* vec_value() const override;
    void push(Cell* c) override;
    void clear() override;

private:
    vector<unique_ptr<Cell>> owned;
    vector<Cell*> value;
};

class MapCell final : public Cell {
public:
    MapCell() = default;

    Type type() const override;
    Cell& call(Cell& c) override;
    Cell& index(int i_) override;
    Cell& index(const string& s_) override;
    string to_string() const override;
    bool is_truthy() const override;
    unordered_map<string, Cell*>* map_value() override;
    const unordered_map<string, Cell*>* map_value() const override;
    void bind(const string& key, Cell* c) override;
    void clear() override;

private:
    unordered_map<string, unique_ptr<Cell>> owned;
    unordered_map<string, Cell*> value;
};

class AnyCell final : public Cell {
public:
    explicit AnyCell(void* value_ = nullptr);

    Type type() const override;
    Cell& call(Cell& c) override;
    Cell& index(int i_) override;
    Cell& index(const string& s_) override;
    string to_string() const override;
    bool is_truthy() const override;

private:
    void* value;
};

class ErrorCell final : public Cell {
public:
    explicit ErrorCell(const char* message_);
    explicit ErrorCell(string message_);

    Type type() const override;
    Cell& call(Cell& c) override;
    Cell& index(int i_) override;
    Cell& index(const string& s_) override;
    string to_string() const override;
    bool is_truthy() const override;
    const string* str_value() const override;

private:
    string message;
};

ostream& operator<<(ostream& os, const Cell& c);
