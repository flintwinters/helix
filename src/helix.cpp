#include <iostream>
#include <builtins.hpp>
#include <core.hpp>
#include <ryml_interface.hpp>

using namespace std;

Cell& add(Cell& c);
Cell& subtract(Cell& c);
Cell& multiply(Cell& c);
Cell& divide(Cell& c);
Cell& if_builtin(Cell& c);

Cell Null;
Cell Zygote;

void initialize_zygote() {
    Zygote.t = Cell::MAP;
    Zygote.m = new unordered_map<string, Cell*>();

    (*Zygote.m)["printout"] = &register_success(new Cell(printout));
    (*Zygote.m)["+"] = &register_success(new Cell(add));
    (*Zygote.m)["-"] = &register_success(new Cell(subtract));
    (*Zygote.m)["*"] = &register_success(new Cell(multiply));
    (*Zygote.m)["/"] = &register_success(new Cell(divide));
    (*Zygote.m)["if"] = &register_success(new Cell(if_builtin));
}

void shutdown_zygote() {
    clear_success_cells();

    delete Zygote.m;
    Zygote.m = nullptr;
    Zygote.t = Cell::ANY;
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
    if (!er) {
        cout << er;
        delete &er;
    }

    shutdown_zygote();
}
