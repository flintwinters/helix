#include <core.hpp>

#include <cstdint>
#include <fstream>
#include <memory>

using namespace std;

namespace {

vector<unique_ptr<Cell>>& rooted_success_cells() {
    static vector<unique_ptr<Cell>> cells;
    return cells;
}

string render_cell(const string& inner, const bool alive) {
    if (alive) {
        return "Cell(\033[1m" + inner + "\033[0m)";
    }
    return "\033[1;31mDead(\033[1m" + inner + "\033[0m\033[1;31m)\033[0m";
}

Cell* rooted_errors_cell() {
    const auto* root = Zygote.map_value();
    if (root == nullptr) {
        return nullptr;
    }

    const auto it = root->find("errors");
    if (it == root->end() || it->second == nullptr) {
        return nullptr;
    }

    return it->second;
}

Cell* search_parents_ptr(Cell& target, const string& name);

Cell* lookup_by_name_ptr(Cell& target, const string& name) {
    if (auto* map = target.map_value()) {
        const auto it = map->find(name);
        if (it != map->end() && it->second != nullptr) {
            return it->second;
        }
        return search_parents_ptr(target, name);
    }

    if (target.type() == Cell::VEC) {
        return search_parents_ptr(target, name);
    }

    return nullptr;
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
    }

    return nullptr;
}

}  // namespace

Cell& register_success(Cell* cell) {
    rooted_success_cells().emplace_back(cell);
    return *cell;
}

void clear_rooted_errors() {
    Cell* errors = rooted_errors_cell();
    auto* values = errors ? errors->vec_value() : nullptr;
    if (values == nullptr) {
        return;
    }

    for (Cell* error : *values) {
        delete error;
    }
    values->clear();
}

void clear_success_cells() {
    rooted_success_cells().clear();
}

Cell::operator bool() const { return alive; }

Cell& Cell::operator()(Cell& c) { return call(c); }

Cell& Cell::operator[](Cell& c) { return index(c); }

Cell& Cell::operator[](const int i_) { return index(i_); }

Cell& Cell::operator[](const char* s_) { return index(string(s_)); }

Cell& Cell::operator[](string s_) { return index(s_); }

Cell& Cell::call(Cell&) {
    return Error("Couldn't call cell");
}

Cell& Cell::index(Cell& c) {
    switch (c.type()) {
    case INT:   return index(c.as_int());
    case STR:   return index(*c.str_value());
    case FUN:   return Error("Can't index with function");
    case VEC:   return Error("Can't index with vector");
    case MAP:   return Error("Can't index with map");
    case ANY:   return Error("Can't index with void*");
    }
    return Error("Couldn't find cell");
}

Cell& Cell::index(const int) { return Error("Couldn't find cell"); }

Cell& Cell::index(const string&) { return Error("Couldn't find cell"); }

int Cell::as_int() const { return 0; }

const string* Cell::str_value() const { return nullptr; }

vector<Cell*>* Cell::vec_value() { return nullptr; }

const vector<Cell*>* Cell::vec_value() const { return nullptr; }

unordered_map<string, Cell*>* Cell::map_value() { return nullptr; }

const unordered_map<string, Cell*>* Cell::map_value() const { return nullptr; }

void Cell::push(Cell*) {}

Cell::operator string() { return to_string(); }

IntCell::IntCell(const int value_) : value(value_) {}

Cell::Type IntCell::type() const { return INT; }

Cell& IntCell::call(Cell&) { return Error("Can't call int"); }

Cell& IntCell::index(const int) { return Error("Can't index int"); }

Cell& IntCell::index(const string&) { return Error("Can't index int"); }

string IntCell::to_string() const {
    return render_cell("\033[35m" + std::to_string(value), alive);
}

bool IntCell::is_truthy() const { return value != 0; }

int IntCell::as_int() const { return value; }

StringCell::StringCell(const char* value_) : value(value_) {}

StringCell::StringCell(string value_) : value(std::move(value_)) {}

Cell::Type StringCell::type() const { return STR; }

Cell& StringCell::call(Cell& c) {
    Cell* target = search_parents_ptr(*this, value);
    if (target != nullptr) {
        return (*target)(c);
    }
    return Error("Couldn't find cell");
}

Cell& StringCell::index(const int) { return Error("Can't index string"); }

Cell& StringCell::index(const string&) { return Error("Can't index string"); }

string StringCell::to_string() const { return render_cell(value, alive); }

bool StringCell::is_truthy() const { return !value.empty(); }

const string* StringCell::str_value() const { return &value; }

FunCell::FunCell(Func value_) : value(value_) {}

Cell::Type FunCell::type() const { return FUN; }

Cell& FunCell::call(Cell& c) { return value(c); }

Cell& FunCell::index(const int) { return Error("Can't index function"); }

Cell& FunCell::index(const string&) { return Error("Can't index function"); }

string FunCell::to_string() const {
    return render_cell("Func@" + std::to_string((uintptr_t)(value)), alive);
}

bool FunCell::is_truthy() const { return true; }

Cell::Type VecCell::type() const { return VEC; }

Cell& VecCell::call(Cell&) {
    if (value.empty() || value.front() == nullptr) {
        return Error("Couldn't call vector");
    }
    return (*value.front())(*this);
}

Cell& VecCell::index(const int i_) {
    if (i_ < 0 || static_cast<size_t>(i_) >= value.size()) {
        return Error("Couldn't find cell");
    }
    return *value[static_cast<size_t>(i_)];
}

Cell& VecCell::index(const string& s_) {
    Cell* found = search_parents_ptr(*this, s_);
    if (found != nullptr) {
        return *found;
    }
    return Error("Couldn't find cell");
}

string VecCell::to_string() const {
    string inner = "Vec#" + std::to_string(value.size());
    for (Cell* c : value) {
        inner += c->to_string();
    }
    return render_cell(inner, alive);
}

bool VecCell::is_truthy() const { return !value.empty(); }

vector<Cell*>* VecCell::vec_value() { return &value; }

const vector<Cell*>* VecCell::vec_value() const { return &value; }

void VecCell::push(Cell* c) { value.push_back(c); }

Cell::Type MapCell::type() const { return MAP; }

Cell& MapCell::call(Cell&) {
    const auto it = value.find("main");
    if (it != value.end() && it->second != nullptr) {
        return (*it->second)(*this);
    }
    return Error("Couldn't find main");
}

Cell& MapCell::index(const int) { return Error("Can't index map with int"); }

Cell& MapCell::index(const string& s_) {
    const auto it = value.find(s_);
    if (it != value.end() && it->second != nullptr) {
        return *it->second;
    }

    Cell* found = search_parents_ptr(*this, s_);
    if (found != nullptr) {
        return *found;
    }
    return Error("Couldn't find cell");
}

string MapCell::to_string() const {
    return render_cell("Map#" + std::to_string(value.size()), alive);
}

bool MapCell::is_truthy() const { return !value.empty(); }

unordered_map<string, Cell*>* MapCell::map_value() { return &value; }

const unordered_map<string, Cell*>* MapCell::map_value() const { return &value; }

AnyCell::AnyCell(void* value_) : value(value_) {}

Cell::Type AnyCell::type() const { return ANY; }

Cell& AnyCell::call(Cell& c) { return c; }

Cell& AnyCell::index(const int) { return Error("Can't index void*"); }

Cell& AnyCell::index(const string&) { return Error("Can't index void*"); }

string AnyCell::to_string() const {
    return render_cell("Any@" + std::to_string((uintptr_t)(value)), alive);
}

bool AnyCell::is_truthy() const { return value != nullptr; }

ostream& operator<<(ostream& os, const Cell& c) {
    return os << c.to_string();
}

Cell& Error(const char* s) {
    Cell* c = new StringCell(s);
    c->alive = false;

    Cell* errors = rooted_errors_cell();
    if (errors != nullptr) {
        errors->push(c);
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
