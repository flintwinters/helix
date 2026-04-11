#pragma once

#include <iosfwd>
#include <string>
#include <unordered_map>
#include <vector>

class Cell;
using Func = Cell& (*)(Cell&);

extern Cell& Zygote;

Cell& Error(const char* s);
Cell& register_success(Cell* cell);
void clear_rooted_errors();
void clear_success_cells();
std::string load_file(const std::string& path);

class Cell {
public:
    enum Type {
        INT, STR, FUN, VEC, MAP, ANY
    };

    bool alive = true;
    std::vector<Cell*> parents;

    Cell() = default;
    Cell(const Cell&) = delete;
    Cell& operator=(const Cell&) = delete;
    virtual ~Cell() = default;

    operator bool() const;

    Cell& operator()(Cell& c);
    Cell& operator[](Cell& c);
    Cell& operator[](int i_);
    Cell& operator[](const char* s_);
    Cell& operator[](std::string s_);

    virtual Type type() const = 0;
    virtual std::string type_name() const = 0;
    virtual Cell& call(Cell& c);
    virtual Cell& index(Cell& c);
    virtual Cell& index(int i_);
    virtual Cell& index(const std::string& s_);
    virtual std::string to_string() const = 0;
    virtual bool is_truthy() const = 0;
    virtual int as_int() const;
    virtual const std::string* str_value() const;
    virtual Func fun_value() const;
    virtual std::vector<Cell*>* vec_value();
    virtual const std::vector<Cell*>* vec_value() const;
    virtual std::unordered_map<std::string, Cell*>* map_value();
    virtual const std::unordered_map<std::string, Cell*>* map_value() const;
    virtual void* any_value() const;
    virtual void push(Cell* c);
    operator std::string();
};

class IntCell final : public Cell {
public:
    explicit IntCell(int value_);

    Type type() const override;
    std::string type_name() const override;
    std::string to_string() const override;
    bool is_truthy() const override;
    int as_int() const override;

private:
    int value;
};

class StringCell final : public Cell {
public:
    explicit StringCell(const char* value_);
    explicit StringCell(std::string value_);

    Type type() const override;
    std::string type_name() const override;
    Cell& call(Cell& c) override;
    std::string to_string() const override;
    bool is_truthy() const override;
    const std::string* str_value() const override;

private:
    std::string value;
};

class FunCell final : public Cell {
public:
    explicit FunCell(Func value_);

    Type type() const override;
    std::string type_name() const override;
    Cell& call(Cell& c) override;
    std::string to_string() const override;
    bool is_truthy() const override;
    Func fun_value() const override;

private:
    Func value;
};

class VecCell final : public Cell {
public:
    VecCell();

    Type type() const override;
    std::string type_name() const override;
    Cell& call(Cell& c) override;
    Cell& index(int i_) override;
    Cell& index(const std::string& s_) override;
    std::string to_string() const override;
    bool is_truthy() const override;
    std::vector<Cell*>* vec_value() override;
    const std::vector<Cell*>* vec_value() const override;
    void push(Cell* c) override;

private:
    std::vector<Cell*> value;
};

class MapCell final : public Cell {
public:
    MapCell();

    Type type() const override;
    std::string type_name() const override;
    Cell& call(Cell& c) override;
    Cell& index(int i_) override;
    Cell& index(const std::string& s_) override;
    std::string to_string() const override;
    bool is_truthy() const override;
    std::unordered_map<std::string, Cell*>* map_value() override;
    const std::unordered_map<std::string, Cell*>* map_value() const override;

private:
    std::unordered_map<std::string, Cell*> value;
};

class AnyCell final : public Cell {
public:
    explicit AnyCell(void* value_ = nullptr);

    Type type() const override;
    std::string type_name() const override;
    Cell& call(Cell& c) override;
    std::string to_string() const override;
    bool is_truthy() const override;
    void* any_value() const override;

private:
    void* value;
};

std::ostream& operator<<(std::ostream& os, const Cell& c);
