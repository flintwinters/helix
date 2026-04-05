#include "core.hpp"

#include <cstdio>
#include <cstdint>
#include <fstream>

using namespace std;

#define returnif(obj) if (obj) {return obj;}
#define me (*this)

Cell::Cell() { t = INT; }

Cell::Cell(const int i_) : i(i_) { t = INT; }

Cell::Cell(Func f_) : f(f_) { t = FUN; }

Cell::Cell(const char* s_) {
    s = new string(s_);
    t = STR;
}

Cell::operator bool() const { return alive; }

Cell& Cell::handlevec() {
    return (*(*v)[0])(me);
}

Cell& Cell::operator()(Cell& c) {
    switch (t) {
    case INT:   return Error("Can't call int");
    case STR:   return Error("Can't call string");
    case FUN:   return f(c);
    case VEC:   return handlevec();
    case MAP:   return c;
    case ANY:   return c;
    }
    printf("\nCouldn't call cell %d\n\n", t);
    return Error("Couldn't call cell");
}

Cell& Cell::operator[](Cell& c) {
    switch (t) {
    case INT:   return Error("Can't index int");
    case STR:   return Error("Can't index string");
    case FUN:   return f(c);
    case VEC:   return *(*v)[c.i];
    case MAP:   return *(*m)[*c.s];
    case ANY:   return Error("Can't index void*");
    }
    returnif(me["parent"][c]);

    return Error("Couldn't find cell");
}

Cell& Cell::operator[](const int i_) {
    Cell c = Cell(i_);
    return me[c];
}

Cell& Cell::operator[](const char* s_) {
    Cell c = Cell(s_);
    return me[c];
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
