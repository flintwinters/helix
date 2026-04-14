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
Cell& run_all(Cell& c);

AnyCell NullStorage;
MapCell ZygoteStorage;

Cell& Null = NullStorage;
Cell& Zygote = ZygoteStorage;

void initialize_zygote() {
    Zygote.bind("errors", new VecCell());
    Zygote.bind("printout", new FunCell(printout));
    Zygote.bind("+", new FunCell(add));
    Zygote.bind("-", new FunCell(subtract));
    Zygote.bind("*", new FunCell(multiply));
    Zygote.bind("/", new FunCell(divide));
    Zygote.bind("if", new FunCell(if_builtin));
    Zygote.bind("all", new FunCell(run_all));
}

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <program.yaml>\n", argv[0]);
        return 1;
    }
    
    initialize_zygote();
    
    const string yaml = load_file(argv[1]);
    Cell& parsed = parse_yaml_to_cells(yaml, Zygote);
    cout << cell_to_yaml_string(parsed);
    
    Cell& er = Zygote.call(Null);
    if (!er) {
        cout << er;
    }
    
    clear_rooted_errors();
}
