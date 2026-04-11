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

AnyCell Null;
MapCell ZygoteRoot;
Cell& Zygote = ZygoteRoot;

void initialize_zygote() {
    auto* bindings = Zygote.map_value();

    Cell& errors = register_success(new VecCell());
    (*bindings)["errors"] = &errors;

    (*bindings)["printout"] = &register_success(new FunCell(printout));
    (*bindings)["+"] = &register_success(new FunCell(add));
    (*bindings)["-"] = &register_success(new FunCell(subtract));
    (*bindings)["*"] = &register_success(new FunCell(multiply));
    (*bindings)["/"] = &register_success(new FunCell(divide));
    (*bindings)["if"] = &register_success(new FunCell(if_builtin));
}

void shutdown_zygote() {
    clear_rooted_errors();
    clear_success_cells();
}

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <program.yaml>\n", argv[0]);
        return 1;
    }

    initialize_zygote();

    const string yaml = load_file(argv[1]);
    Cell& parsed = parse_yaml_to_cells(yaml, Zygote);
    (*Zygote.map_value())["main"] = &parsed;
    cout << cell_to_yaml_string(parsed);

    Cell& er = Zygote(Null);
    if (!er) {
        cout << er;
    }

    shutdown_zygote();
}
