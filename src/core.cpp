#include <core.hpp>

#include <cstdio>
#include <cstdint>
#include <fstream>
#include <memory>

using namespace std;

Cell& lookup_by_index(Cell& target, const int index);
Cell& lookup_by_name(Cell& target, const string& name);
Cell* lookup_by_name_ptr(Cell& target, const string& name);
Cell* search_parents_ptr(Cell& target, const string& name);

vector<unique_ptr<Cell>>& rooted_success_cells() {
    static vector<unique_ptr<Cell>> cells;
    return cells;
}

Cell* rooted_errors_cell() {
    if (Zygote.t != Cell::MAP || Zygote.m == nullptr) {
        return nullptr;
    }

    const auto it = Zygote.m->find("errors");
    if (it == Zygote.m->end() || it->second == nullptr) {
        return nullptr;
    }

    return it->second;
}

Cell& register_success(Cell* cell) {
    rooted_success_cells().emplace_back(cell);
    return *cell;
}

void clear_rooted_errors() {
    Cell* errors = rooted_errors_cell();
    if (errors == nullptr || errors->t != Cell::VEC || errors->v == nullptr) {
        return;
    }

    for (Cell* error : *errors->v) {
        delete error;
    }
    errors->v->clear();
}

void clear_success_cells() {
    rooted_success_cells().clear();
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

Cell* search_parents_ptr(Cell& target, const string& name) {
    for (Cell* parent : target.parents) {
        if (parent == nullptr) {
            continue;
        }

        Cell* inherited = lookup_by_name_ptr(*parent, name);
        if (inherited != nullptr) {
            return inherited;
        }

        Cell* ancestor = search_parents_ptr(*parent, name);
        if (ancestor != nullptr) {
            return ancestor;
        }
    }

    return nullptr;
}

Cell* lookup_by_name_ptr(Cell& target, const string& name) {
    switch (target.t) {
    case Cell::INT:   return nullptr;
    case Cell::STR:   return nullptr;
    case Cell::FUN:   return nullptr;
    case Cell::VEC:   return search_parents_ptr(target, name);
    case Cell::MAP: {
        const auto it = target.m->find(name);
        if (it != target.m->end() && it->second != nullptr) {
            return it->second;
        }
        return search_parents_ptr(target, name);
    }
    case Cell::ANY:   return nullptr;
    }
    return nullptr;
}

Cell& lookup_by_name(Cell& target, const string& name) {
    switch (target.t) {
    case Cell::INT:   return Error("Can't index int");
    case Cell::STR:   return Error("Can't index string");
    case Cell::FUN:   return Error("Can't index function");
    case Cell::VEC: {
        Cell* found = search_parents_ptr(target, name);
        if (found != nullptr) {
            return *found;
        }
        return Error("Couldn't find cell");
    }
    case Cell::MAP: {
        Cell* found = lookup_by_name_ptr(target, name);
        if (found != nullptr) {
            return *found;
        }
        return Error("Couldn't find cell");
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
        Cell* target = search_parents_ptr(*this, *s);
        if (target != nullptr) {
            return (*target)(c);
        }
        return Error("Couldn't find cell");
    }
    case FUN:   return f(c);
    case VEC:   return (*(*v)[0])(*this);
    case MAP:   {
        const auto it = m->find("main");
        if (it != m->end() && it->second != nullptr) {
            return (*it->second)(*this);
        }
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

bool Cell::is_truthy() const {
    switch (t) {
    case INT:
        return i != 0;
    case STR:
        return s != nullptr && !s->empty();
    case FUN:
        return true;
    case VEC:
        return v != nullptr && !v->empty();
    case MAP:
        return m != nullptr && !m->empty();
    case ANY:
        return a != nullptr;
    }
    return false;
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

    Cell* errors = rooted_errors_cell();
    if (errors != nullptr && errors->t == Cell::VEC && errors->v != nullptr) {
        errors->v->push_back(c);
    }

    return *c;
}

string load_file(const string& path) {
    ifstream file(path, ios::binary | ios::ate);
    streamsize size = file.tellg();
    file.seekg(0, ios::beg);

    string buffer(size, '\0');
    file.read(&buffer[0], size);

    return buffer;
}
