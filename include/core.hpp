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

Cell* make_int_cell(int value);
Cell* make_string_cell(const char* value);
Cell* make_string_cell(std::string value);
Cell* make_fun_cell(Func value);
Cell* make_vec_cell();
Cell* make_map_cell();
Cell* make_any_cell(void* value = nullptr);

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
    virtual Cell& call(Cell& c);
    virtual Cell& index(Cell& c);
    virtual Cell& index(int i_);
    virtual Cell& index(const std::string& s_);
    virtual std::string to_string() const = 0;
    virtual bool is_truthy() const = 0;
    virtual int as_int() const;
    virtual const std::string* str_value() const;
    virtual std::vector<Cell*>* vec_value();
    virtual const std::vector<Cell*>* vec_value() const;
    virtual std::unordered_map<std::string, Cell*>* map_value();
    virtual const std::unordered_map<std::string, Cell*>* map_value() const;
    virtual void push(Cell* c);
    operator std::string();
};

std::ostream& operator<<(std::ostream& os, const Cell& c);
