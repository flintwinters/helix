#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>

#include <c4/yml/parse.hpp>

#include "core.cpp"

static string read_file_text(const char* path)
{
    ifstream input(path, ios::binary);
    if(!input)
    {
        throw runtime_error(string("failed to open file: ") + path);
    }

    return {istreambuf_iterator<char>(input), istreambuf_iterator<char>()};
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
        const string file_text = read_file_text(argv[1]);
        const c4::csubstr yaml_text(file_text.data(), file_text.size());
        c4::yml::Tree tree = c4::yml::parse_in_arena(argv[1], yaml_text);
        c4::yml::ConstNodeRef root = tree.rootref();
        const CellPtr root_cell = cell_from_ryml_node(root);
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
