#include <iostream>
#include <vector>
#include <builtins.hpp>
#include <core.hpp>
#include <ryml_interface.hpp>

using namespace std;

int main(int argc, char** argv) {
    if (argc < 2) {
        fprintf(stderr, "usage: %s <program.yaml>\n", argv[0]);
        return 1;
    }

    const string yaml = load_file(argv[1]);
    Cell& parsed = parse_yaml_to_cells(yaml);
    ryml::Tree roundtrip = cell_to_yaml(parsed);
    vector<char> yaml_buffer(yaml.size() * 4 + 64, '\0');
    const ryml::substr emitted = ryml::emit_yaml(roundtrip, ryml::substr(yaml_buffer.data(), yaml_buffer.size()));
    cout.write(emitted.str, emitted.len);

    parsed["try_this"][0].t = Cell::FUN;
    parsed["try_this"][0].f = printout;

    Cell v = parsed["try_this"];
    Cell a = Cell(0);
    v(a);
    delete (*v.v)[1];
    delete &parsed;
}
