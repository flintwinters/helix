#include <core.hpp>

#include <cstdio>
#include <cstdint>
#include <fstream>
#include <memory>

using namespace std;

Cell& lookup_by_index(Cell& target, const int index);
Cell& lookup_by_name(Cell& target, const string& name);
Cell& search_parents(Cell& target, const string& name);

namespace {
vector<unique_ptr<Cell>>& arena_cells() {
    static vector<unique_ptr<Cell>> cells;
    return cells;
}
}

Cell& allocate_in_arena(Cell* cell) {
    arena_cells().emplace_back(cell);
    if (Arena.t == Cell::VEC && Arena.v != nullptr) {
        Arena.push(cell);
    }
    return *cell;
}

void clear_arena() {
    if (Arena.t != Cell::VEC || Arena.v == nullptr) {
        arena_cells().clear();
        return;
    }

    arena_cells().clear();
    Arena.v->clear();
}

Cell& lookup_by_index(Cell& target, const int index) {
    switch (target.t) {
    case Cell::INT:   return Error("Can't index int");
    case Cell::STR:   return Error("Can't index string");
    case Cell::FUN:   return Error("Can't index function");
    case Cell::VEC:   return *(*target.v)[index];
    case Cell::MAP:   return Error("Can't index map with int");
    case Cell::ANY:   return Error("Can't index void*");
    }
    return Error("Couldn't find cell");
}

Cell& search_parents(Cell& target, const string& name) {
    for (Cell* parent : target.parents) {
        if (parent == nullptr) {
            continue;
        }

        Cell& inherited = lookup_by_name(*parent, name);
        if (inherited) {
            return inherited;
        }

        Cell& ancestor = search_parents(*parent, name);
        if (ancestor) {
            return ancestor;
        }
    }

    return Error("Couldn't find cell");
}

Cell& lookup_by_name(Cell& target, const string& name) {
    switch (target.t) {
    case Cell::INT:   return Error("Can't index int");
    case Cell::STR:   return Error("Can't index string");
    case Cell::FUN:   return Error("Can't index function");
    case Cell::VEC:   return search_parents(target, name);
    case Cell::MAP: {
        const auto it = target.m->find(name);
        if (it != target.m->end() && it->second != nullptr) {
            return *it->second;
        }
        return search_parents(target, name);
    }
    case Cell::ANY:   return Error("Can't index void*");
    }
    return Error("Couldn't find cell");
}

Cell::Cell() { t = INT; }

Cell::Cell(const int i_) : i(i_) { t = INT; }

Cell::Cell(Func f_) : f(f_) { t = FUN; }

Cell::Cell(const char* s_) {
    s = new string(s_);
    t = STR;
}

Cell::operator bool() const { return alive; }

Cell& Cell::operator()(Cell& c) {
    switch (t) {
    case INT:   return Error("Can't call int");
    case STR: {
        Cell& target = search_parents(*this, *s);
        if (target) {
            return target(c);
        }
        return target;
    }
    case FUN:   return f(c);
    case VEC:   return (*(*v)[0])(*this);
    case MAP:   {
        auto a = (*m)["main"];
        if (a) {return (*a)(*this);}
        return Error("Couldn't find main");
    }
    case ANY:   return c;
    }
    printf("\nCouldn't call cell %d\n\n", t);
    return Error("Couldn't call cell");
}

Cell& Cell::operator[](Cell& c) {
    switch (c.t) {
    case INT:   return lookup_by_index(*this, c.i);
    case STR:   return lookup_by_name(*this, *c.s);
    case FUN:   return Error("Can't index with function");
    case VEC:   return Error("Can't index with vector");
    case MAP:   return Error("Can't index with map");
    case ANY:   return Error("Can't index with void*");
    }
    return Error("Couldn't find cell");
}

Cell& Cell::operator[](const int i_) {
    return lookup_by_index(*this, i_);
}

Cell& Cell::operator[](const char* s_) {
    return lookup_by_name(*this, s_);
}

Cell& Cell::operator[](string s_) {
    return lookup_by_name(*this, s_);
}

Cell::~Cell() {
    switch (t) {
    case INT:   break;
    case STR:   delete s; break;
    case FUN:   break;
    case VEC:   delete v; break;
    case MAP:   delete m; break;
    case ANY:   break;
    }
}

string Cell::to_string() const {
    string inner = "Unknown";
    switch (t) {
    case INT: inner = "\033[35m" + std::to_string(i); break;
    case STR: inner = (s ? *s : "null"); break;
    case FUN: inner = "Func@" + std::to_string((uintptr_t)(f)); break;
    case VEC:
        inner = "Vec#" + std::to_string(v ? v->size() : 0);
        for (Cell* c : *v) {
            inner += c->to_string();
        }
        break;
    case MAP: inner = "Map#" + std::to_string(m ? m->size() : 0); break;
    case ANY: inner = "Any@" + std::to_string((uintptr_t)(a)); break;
    }

    if (alive) {
        return "Cell(\033[1m" + inner + "\033[0m)";
    }
    return "\033[1;31mDead(\033[1m" + inner + "\033[0m\033[1;31m)\033[0m";
}

Cell::operator string() { return to_string(); }

void Cell::push(Cell* c) {
    v->push_back(c);
}

ostream& operator<<(ostream& os, const Cell& c) {
    return os << c.to_string();
}

Cell& Error(const char* s) {
    Cell* c = new Cell();
    c->alive = false;
    c->t = Cell::STR;
    c->s = new string(s);
    return allocate_in_arena(c);
}

string load_file(const string& path) {
    ifstream file(path, ios::binary | ios::ate);
    streamsize size = file.tellg();
    file.seekg(0, ios::beg);

    string buffer(size, '\0');
    file.read(&buffer[0], size);

    return buffer;
}
