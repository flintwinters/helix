#pragma once

#include <iosfwd>
#include <string>
#include <unordered_map>
#include <vector>

class Cell;
using Func = Cell& (*)(Cell&);

extern Cell Zygote;
extern Cell Arena;

Cell& Error(const char* s);
Cell& allocate_in_arena(Cell* cell);
void clear_arena();
std::string load_file(const std::string& path);

class Cell {
public:
    enum Type {
        INT, STR, FUN, VEC, MAP, ANY
    };

    Type t = INT;
    union {
        int i;
        std::string* s;
        Func f;
        std::vector<Cell*>* v;
        std::unordered_map<std::string, Cell*>* m;
        void* a;
    };
    bool alive = true;
    std::vector<Cell*> parents;

    Cell();
    explicit Cell(int i_);
    explicit Cell(Func f_);
    explicit Cell(const char* s_);

    operator bool() const;

    Cell& handlevec();
    Cell& operator()(Cell& c);
    Cell& operator[](Cell& c);
    Cell& operator[](int i_);
    Cell& operator[](const char* s_);
    Cell& operator[](std::string s_);
    ~Cell();

    std::string to_string() const;
    operator std::string();

    void push(Cell* c);
};

std::ostream& operator<<(std::ostream& os, const Cell& c);
