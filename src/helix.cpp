#include <iostream>
#include <builtins.hpp>
#include <core.hpp>
#include <ryml_interface.hpp>

using namespace std;

Cell& add(Cell& c);
Cell& subtract(Cell& c);
Cell& multiply(Cell& c);
Cell& divide(Cell& c);

Cell Zygote;

void initialize_zygote() {
    Zygote.t = Cell::MAP;
    Zygote.m = new unordered_map<string, Cell*>();

    (*Zygote.m)["printout"] = new Cell(printout);
    (*Zygote.m)["+"] = new Cell(add);
    (*Zygote.m)["-"] = new Cell(subtract);
    (*Zygote.m)["*"] = new Cell(multiply);
    (*Zygote.m)["/"] = new Cell(divide);
}

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <program.yaml>\n", argv[0]);
        return 1;
    }

    initialize_zygote();

    const string yaml = load_file(argv[1]);
    Cell& parsed = parse_yaml_to_cells(yaml);
    (*Zygote.m)["main"] = &parsed;
    cout << cell_to_yaml_string(parsed);

    parsed["try_this"][0].t = Cell::FUN;
    parsed["try_this"][0].f = printout;

    Cell v = parsed["try_this"];
    Cell a = Cell(0);
    v(a);
    delete (*v.v)[1];
    delete &parsed;
}
