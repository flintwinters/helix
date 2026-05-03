#include <ryml_interface.hpp>

#include <charconv>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>

#include <c4/yml/emit.hpp>
#include <c4/yml/parse.hpp>
#include <c4/yml/std/string.hpp>

using namespace std;

static string read_yaml_file_text(const char* path) {
    ifstream input(path, ios::binary);
    if (!input) {
        throw runtime_error(string("failed to open file: ") + path);
    }

    return {istreambuf_iterator<char>(input), istreambuf_iterator<char>()};
}

static string ryml_text_to_string(c4::csubstr text) {
    return {text.str, text.len};
}

static CellPtr scalar_cell_from_ryml(c4::csubstr scalar) {
    const string text = ryml_text_to_string(scalar);
    int64_t integer_value = 0;
    const char* begin = text.data();
    const char* end = begin + text.size();
    if (!text.empty()) {
        const auto result = from_chars(begin, end, integer_value);
        if (result.ec == errc{} && result.ptr == end) {
            return make_shared<IntCell>(integer_value);
        }
    }
    return make_shared<StrCell>(text);
}

CellPtr cell_from_ryml_node(c4::yml::ConstNodeRef node) {
    while ((node.is_stream() || node.is_doc()) && node.has_children()) {
        node = node.first_child();
    }

    if (node.is_map()) {
        unordered_map<string, CellPtr> fields {};
        for (const auto child : node.children()) {
            fields.emplace(ryml_text_to_string(child.key()), cell_from_ryml_node(child));
        }
        return make_shared<MapCell>(move(fields));
    }

    if (node.is_seq()) {
        vector<CellPtr> elements {};
        elements.reserve(static_cast<size_t>(node.num_children()));
        for (const auto child : node.children()) {
            elements.push_back(cell_from_ryml_node(child));
        }
        return make_shared<VecCell>(move(elements));
    }

    if (node.has_val()) {
        return scalar_cell_from_ryml(node.val());
    }

    return make_shared<StrCell>();
}

shared_ptr<MapCell> load_root_cell_from_yaml_file(const char* path) {
    const string file_text = read_yaml_file_text(path);
    const c4::csubstr yaml_text(file_text.data(), file_text.size());
    c4::yml::Tree tree = c4::yml::parse_in_arena(path, yaml_text);
    CellPtr root_cell = cell_from_ryml_node(tree.rootref());
    if (!root_cell || root_cell->type != Cell::Type::map) {
        throw runtime_error("top-level YAML document must be a mapping");
    }
    return static_pointer_cast<MapCell>(move(root_cell));
}

void write_cell_to_ryml_node(ConstCellPtr cell, c4::yml::NodeRef node) {
    if (!cell) {
        node << "null";
        return;
    }

    switch (cell->type) {
    case Cell::Type::map: {
        node |= c4::yml::MAP;
        const auto& map_cell = static_cast<const MapCell&>(*cell);
        for (const auto& [key, value] : map_cell.value) {
            auto child = node.append_child();
            child << c4::yml::key(key);
            write_cell_to_ryml_node(value, child);
        }
        return;
    }
    case Cell::Type::vec: {
        node |= c4::yml::SEQ;
        const auto& vec_cell = static_cast<const VecCell&>(*cell);
        for (const CellPtr& value : vec_cell.value) {
            auto child = node.append_child();
            write_cell_to_ryml_node(value, child);
        }
        return;
    }
    case Cell::Type::integer:
        node << static_cast<const IntCell&>(*cell).value;
        return;
    case Cell::Type::string:
        node << static_cast<const StrCell&>(*cell).value;
        return;
    case Cell::Type::function:
        node << "<function>";
        return;
    case Cell::Type::signal:
    case Cell::Type::return_signal: {
        node |= c4::yml::MAP;
        const auto& sig_cell = static_cast<const SigCell&>(*cell);
        node["signal_type"] << (cell->type == Cell::Type::return_signal ? "return" : "signal");
        if (sig_cell.value) {
            write_cell_to_ryml_node(sig_cell.value, node["value"]);
        }
        return;
    }
    case Cell::Type::error_signal: {
        node |= c4::yml::MAP;
        const auto& err_cell = static_cast<const ErrCell&>(*cell);
        node["signal_type"] << "error";
        node["message"] << err_cell.message;
        if (err_cell.value) {
            write_cell_to_ryml_node(err_cell.value, node["value"]);
        }
        return;
    }
    case Cell::Type::base:
    default:
        node << "<cell>";
        return;
    }
}

c4::yml::Tree ryml_tree_from_cell(ConstCellPtr root_cell) {
    c4::yml::Tree tree {};
    write_cell_to_ryml_node(root_cell, tree.rootref());
    return tree;
}

string emit_yaml_from_cell(ConstCellPtr root_cell) {
    return c4::yml::emitrs_yaml<string>(ryml_tree_from_cell(root_cell));
}
