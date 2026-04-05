#include <initializer_list>
#include <cerrno>
#include <cstdlib>
#include <iostream>
#include <span>
#include <string>
#include <unordered_map>
#include <vector>
#include <fstream>
#include <ryml.hpp>
using namespace std;

#define returnif(obj) if (obj) {return obj;}
#define me (*this)

class Cell;
typedef Cell& (*Func)(Cell&);
Cell& Error(const char* s);
void cell_to_yaml_node(const Cell& cell, ryml::NodeRef* node);
class Cell {
public:
    enum Type {
        INT, STR, FUN, VEC, MAP, ANY
    };
    enum Type t = INT;
    union {
        int i;
        string* s;
        Func f;
        vector<Cell*>* v;
        unordered_map<string, Cell*>* m;
        void* a;
    };
    bool alive = true;
    Cell() {t = INT;}
    Cell(const int i_) : i(i_) {t = INT;}
    Cell(Func f_) : f(f_) {t = FUN;}
    Cell(const char* s_) {
        s = new string(s_);
        t = STR;
    }

    operator bool() const {return alive;}

    Cell& handlevec() {
        return (*(*v)[0])(me);
    }

    Cell& operator()(Cell& c) {
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
    
    Cell& operator[](Cell& c) {
        switch(t) {
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
    Cell& operator[](const int i_) {Cell c = Cell(i_); return me[c];}
    Cell& operator[](const char* s_) {Cell c = Cell(s_); return me[c];}
    
    ~Cell() {
        switch (t) {
        case INT:   break;
        case STR:   delete s; break;
        case FUN:   break;
        case VEC:   delete v; break;
        case MAP:   delete m; break;
        case ANY:   break;
        }
    }

    string to_string() const {
        string inner = "Unknown";
        switch (t) {
        case INT: inner = "\033[35m" + (std::to_string(i)); break;
        case STR: inner = (s ? *s : "null"); break;
        case FUN: inner = ("Func@" + std::to_string((uintptr_t)(f))); break;
        case VEC: inner = ("Vec#" + std::to_string(v ? v->size() : 0)); break;
        case MAP: inner = ("Map#" + std::to_string(m ? m->size() : 0)); break;
        case ANY: inner = ("Any@" + std::to_string((uintptr_t)(a))); break;
        }

        if (alive) {
            return "Cell(\033[1m" + inner + "\033[0m)";
        } else {
            return "\033[1;31mDead(\033[1m" + inner + "\033[0m\033[1;31m)\033[0m";
        }
    }
    operator string() {return to_string();}

    void push(Cell* c) {
        v->push_back(c);
    }

    ryml::Tree to_yaml() const;
};

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

Cell& printout(Cell& c) {
    cout << c.to_string() << "\n";
    return c;
}

void cell_to_yaml_node(const Cell& cell, ryml::NodeRef* node) {
    switch (cell.t) {
    case Cell::INT:
        *node << cell.i;
        return;
    case Cell::STR:
        if (cell.s) {
            node->set_val(ryml::csubstr(cell.s->data(), cell.s->size()));
        } else {
            const char* empty = "";
            node->set_val(ryml::csubstr(empty, static_cast<size_t>(0)));
        }
        return;
    case Cell::VEC:
        *node |= ryml::SEQ;
        for (Cell* child : *cell.v) {
            ryml::NodeRef child_node = node->append_child();
            cell_to_yaml_node(*child, &child_node);
        }
        return;
    case Cell::MAP:
        *node |= ryml::MAP;
        for (const auto& [key, value] : *cell.m) {
            ryml::NodeRef child_node = node->append_child();
            child_node.set_key(ryml::csubstr(key.data(), key.size()));
            cell_to_yaml_node(*value, &child_node);
        }
        return;
    case Cell::FUN:
        node->set_val("FUN");
        return;
    case Cell::ANY:
        node->set_val("ANY");
        return;
    }
}

ryml::Tree Cell::to_yaml() const {
    ryml::Tree tree;
    ryml::NodeRef root = tree.rootref();
    cell_to_yaml_node(*this, &root);
    return tree;
}

Cell* yaml_node_to_cell(const ryml::ConstNodeRef& node) {
    if (node.is_map()) {
        Cell* cell = new Cell();
        cell->t = Cell::MAP;
        cell->m = new unordered_map<string, Cell*>();
        for (const ryml::ConstNodeRef& child : node.children()) {
            const string key(child.key().str, child.key().len);
            (*cell->m)[key] = yaml_node_to_cell(child);
        }
        return cell;
    }

    if (node.is_seq()) {
        Cell* cell = new Cell();
        cell->t = Cell::VEC;
        cell->v = new vector<Cell*>();
        for (const ryml::ConstNodeRef& child : node.children()) {
            cell->v->push_back(yaml_node_to_cell(child));
        }
        return cell;
    }

    const string value(node.val().str, node.val().len);
    char* end = nullptr;
    errno = 0;
    const long parsed = std::strtol(value.c_str(), &end, 10);
    const bool is_integer = !value.empty() && end == value.c_str() + value.size() && errno == 0;
    if (is_integer) {
        return new Cell(static_cast<int>(parsed));
    }

    return new Cell(value.c_str());
}

Cell& parse_yaml_to_cells(const string& yaml) {
    const ryml::csubstr yaml_view(yaml.data(), yaml.size());
    ryml::Tree tree = ryml::parse_in_arena(yaml_view);
    return *yaml_node_to_cell(tree.rootref());
}

// Loads an entire file into memory for parsing.
string load_file(const string& path) {
    ifstream file(path, ios::binary | ios::ate);
    streamsize size = file.tellg();
    file.seekg(0, ios::beg);

    string buffer(size, '\0');
    file.read(&buffer[0], size);
    
    return buffer;
}

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <program.yaml>\n", argv[0]);
        return 1;
    }

    string yaml = load_file(argv[1]);
    Cell& parsed = parse_yaml_to_cells(yaml);
    ryml::Tree roundtrip = parsed.to_yaml();
    vector<char> yaml_buffer(yaml.size() * 4 + 64, '\0');
    const ryml::substr emitted = ryml::emit_yaml(roundtrip, ryml::substr(yaml_buffer.data(), yaml_buffer.size()));
    cout.write(emitted.str, emitted.len);
    delete &parsed;
    return 0;

    Cell c = Cell(printout);
    Cell i = Cell(12);
    Cell& a = c(i);
    
    Cell v = Cell();
    v.t = Cell::VEC;
    v.v = new vector<Cell*>();
    v.push(&c);
    v.push(new Cell(15));
    v(a);
    delete (*v.v)[1];
}
