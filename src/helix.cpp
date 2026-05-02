#include <iostream>
#include <memory>
#include <stdexcept>

#include <ryml_interface.hpp>

static bool is_null_cell(ConstCellPtr cell)
{
    if(!cell)
    {
        return true;
    }

    if(cell->type != Cell::Type::string)
    {
        return false;
    }

    const auto& str_cell = static_cast<const StrCell&>(*cell);
    return str_cell.value == "null";
}

static void run_identity_main(CellPtr root_cell)
{
    if(!root_cell || root_cell->type != Cell::Type::map)
    {
        throw runtime_error("top-level YAML document must be a mapping");
    }

    auto& root = static_cast<MapCell&>(*root_cell);
    const auto main_it = root.value.find("main");
    if(main_it == root.value.end())
    {
        throw runtime_error("program is missing a main entrypoint");
    }

    if(!is_null_cell(main_it->second))
    {
        throw runtime_error("native scaffold only supports `main: null` currently");
    }

    auto state = make_shared<MapCell>();
    state->value["status"] = make_shared<StrCell>("finished");
    state->value["result"] = make_shared<StrCell>("null");
    state->value["frames"] = make_shared<VecCell>();
    root.value["state"] = move(state);
}

int main(int argc, char* argv[])
{
    if(argc != 2)
    {
        std::cerr << "usage: ./helix <program.yaml>\n";
        return 1;
    }

    try
    {
        CellPtr root_cell = load_root_cell_from_yaml_file(argv[1]);
        run_identity_main(root_cell);
        std::cout << emit_yaml_from_cell(root_cell);
    }
    catch(const exception& error)
    {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }

    return 0;
}
