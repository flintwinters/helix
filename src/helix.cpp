#include <iostream>
#include <builtins.hpp>
#include <core.hpp>
#include <ryml_interface.hpp>

using namespace std;

Cell& add(Cell& c);
Cell& subtract(Cell& c);
Cell& multiply(Cell& c);
Cell& divide(Cell& c);

Cell Null;
Cell Zygote;
Cell Arena;

void initialize_zygote() {
    Arena.t = Cell::VEC;
    Arena.v = new vector<Cell*>();

    Zygote.t = Cell::MAP;
    Zygote.m = new unordered_map<string, Cell*>();
    (*Zygote.m)["arena"] = &Arena;

    (*Zygote.m)["printout"] = &allocate_in_arena(new Cell(printout));
    (*Zygote.m)["+"] = &allocate_in_arena(new Cell(add));
    (*Zygote.m)["-"] = &allocate_in_arena(new Cell(subtract));
    (*Zygote.m)["*"] = &allocate_in_arena(new Cell(multiply));
    (*Zygote.m)["/"] = &allocate_in_arena(new Cell(divide));
}

void shutdown_zygote() {
    clear_arena();

    delete Zygote.m;
    Zygote.m = nullptr;
    Zygote.t = Cell::ANY;

    delete Arena.v;
    Arena.v = nullptr;
    Arena.t = Cell::ANY;
}

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <program.yaml>\n", argv[0]);
        return 1;
    }

    initialize_zygote();

    const string yaml = load_file(argv[1]);
    Cell& parsed = parse_yaml_to_cells(yaml, Zygote);
    (*Zygote.m)["main"] = &parsed;
    cout << cell_to_yaml_string(parsed);

    Cell& er = Zygote(Null);
    if (!er) {cout << er;}

    shutdown_zygote();
}
