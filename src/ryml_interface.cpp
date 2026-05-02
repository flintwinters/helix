#include <charconv>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>

#include <c4/yml/node.hpp>
#include <c4/yml/parse.hpp>

#include "core.cpp"

using namespace std;

inline string read_yaml_file_text(const char* path)
{
    ifstream input(path, ios::binary);
    if(!input)
    {
        throw runtime_error(string("failed to open file: ") + path);
    }

    return {istreambuf_iterator<char>(input), istreambuf_iterator<char>()};
}

inline string ryml_text_to_string(c4::csubstr text)
{
    return {text.str, text.len};
}

inline CellPtr scalar_cell_from_ryml(c4::csubstr scalar)
{
    const string text = ryml_text_to_string(scalar);
    int64_t integer_value = 0;
    const char* begin = text.data();
    const char* end = begin + text.size();
    if(!text.empty())
    {
        const auto result = from_chars(begin, end, integer_value);
        if(result.ec == errc{} && result.ptr == end)
        {
            return make_shared<IntCell>(integer_value);
        }
    }
    return make_shared<StrCell>(text);
}

inline CellPtr cell_from_ryml_node(c4::yml::ConstNodeRef node)
{
    while((node.is_stream() || node.is_doc()) && node.has_children())
    {
        node = node.first_child();
    }

    if(node.is_map())
    {
        unordered_map<string, CellPtr> fields {};
        for(const auto child : node.children())
        {
            fields.emplace(ryml_text_to_string(child.key()), cell_from_ryml_node(child));
        }
        return make_shared<MapCell>(move(fields));
    }

    if(node.is_seq())
    {
        vector<CellPtr> elements {};
        elements.reserve(static_cast<size_t>(node.num_children()));
        for(const auto child : node.children())
        {
            elements.push_back(cell_from_ryml_node(child));
        }
        return make_shared<VecCell>(move(elements));
    }

    if(node.has_val())
    {
        return scalar_cell_from_ryml(node.val());
    }

    return make_shared<StrCell>();
}

inline CellPtr load_root_cell_from_yaml_file(const char* path)
{
    const string file_text = read_yaml_file_text(path);
    const c4::csubstr yaml_text(file_text.data(), file_text.size());
    c4::yml::Tree tree = c4::yml::parse_in_arena(path, yaml_text);
    return cell_from_ryml_node(tree.rootref());
}
