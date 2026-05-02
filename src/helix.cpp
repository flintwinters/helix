#include <iostream>
#include <stdexcept>
#include "ryml_interface.cpp"

int main(int argc, char* argv[])
{
    if(argc != 2)
    {
        std::cerr << "usage: ./helix <program.yaml>\n";
        return 1;
    }

    try
    {
        const CellPtr root_cell = load_root_cell_from_yaml_file(argv[1]);
        std::cout << "loaded " << argv[1]
                  << " into cell type " << static_cast<int>(root_cell->type)
                  << '\n';
    }
    catch(const exception& error)
    {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }

    return 0;
}
