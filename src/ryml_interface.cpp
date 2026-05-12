#include <ryml_interface.hpp>

#include <charconv>
#include <filesystem>
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
    if (text == "null") {
        return make_shared<NilCell>();
    }

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

static void attach_parent_if_missing(const CellPtr& child, const CellPtr& parent) {
    if (!child || !parent || child->parent) {
        return;
    }

    child->parent = parent;
}

static filesystem::path resolve_include_path(const filesystem::path& source_path, const string& include_name) {
    const filesystem::path include_path(include_name);
    if (include_path.is_absolute()) {
        return include_path;
    }

    return source_path.parent_path() / include_path;
}

static string include_binding_name(const filesystem::path& include_path) {
    return include_path.stem().string();
}

CellPtr cell_from_ryml_node(c4::yml::ConstNodeRef node) {
    while ((node.is_stream() || node.is_doc()) && node.has_children()) {
        node = node.first_child();
    }

    if (node.is_map()) {
        bool has_main = false;
        for (const auto child : node.children()) {
            if (ryml_text_to_string(child.key()) == "main") {
                has_main = true;
                break;
            }
        }

        shared_ptr<MapCell> map_cell = has_main
            ? static_pointer_cast<MapCell>(make_shared<VmCell>())
            : static_pointer_cast<MapCell>(make_shared<ScopeCell>());
        for (const auto child : node.children()) {
            CellPtr child_cell = cell_from_ryml_node(child);
            attach_parent_if_missing(child_cell, map_cell);
            map_cell->value.emplace(ryml_text_to_string(child.key()), move(child_cell));
        }
        return map_cell;
    }

    if (node.is_seq()) {
        shared_ptr<VecCell> vec_cell = make_shared<VecCell>();
        vec_cell->value.reserve(static_cast<size_t>(node.num_children()));
        for (const auto child : node.children()) {
            CellPtr child_cell = cell_from_ryml_node(child);
            attach_parent_if_missing(child_cell, vec_cell);
            vec_cell->value.push_back(move(child_cell));
        }
        return vec_cell;
    }

    if (node.has_val()) {
        return scalar_cell_from_ryml(node.val());
    }

    return make_shared<StrCell>();
}

static void expand_includes_in_root_map(const shared_ptr<VmCell>& root_cell, const filesystem::path& source_path) {
    if (!root_cell) {
        return;
    }

    unordered_map<string, CellPtr>::const_iterator include_it = root_cell->value.find("include");
    if (include_it == root_cell->value.end()) {
        return;
    }

    const CellPtr include_cell = include_it->second;
    if (!include_cell || include_cell->type != Cell::Type::vec) {
        throw runtime_error("include must be a YAML sequence");
    }

    const VecCell& include_list = static_cast<const VecCell&>(*include_cell);
    for (const CellPtr& include_entry : include_list.value) {
        if (!include_entry || include_entry->type != Cell::Type::string) {
            throw runtime_error("include entries must be strings");
        }

        const string& include_name = static_cast<const StrCell&>(*include_entry).value;
        const filesystem::path include_path = resolve_include_path(source_path, include_name);
        const shared_ptr<VmCell> included_root = load_root_cell_from_yaml_file(include_path.c_str());
        root_cell->set(include_binding_name(include_path), included_root);
    }
}

shared_ptr<VmCell> load_root_cell_from_yaml_file(const char* path) {
    const string file_text = read_yaml_file_text(path);
    const c4::csubstr yaml_text(file_text.data(), file_text.size());
    c4::yml::Tree tree = c4::yml::parse_in_arena(path, yaml_text);
    CellPtr root_cell = cell_from_ryml_node(tree.rootref());
    shared_ptr<VmCell> root_vm = expect_vm_cell(root_cell);
    if (!root_vm) {
        shared_ptr<MapCell> root_map = expect_map_cell(root_cell);
        if (!root_map) {
            throw runtime_error("top-level YAML document must be a mapping");
        }

        root_vm = make_shared<VmCell>();
        for (auto& [key, value] : root_map->value) {
            if (value) {
                value->parent = nullptr;
            }
            root_vm->set(key, value);
        }
    }

    if (!root_vm) {
        throw runtime_error("top-level YAML document must be a mapping");
    }
    expand_includes_in_root_map(root_vm, filesystem::path(path));
    return root_vm;
}

void write_cell_to_ryml_node(ConstCellPtr cell, c4::yml::NodeRef node) {
    if (!cell) {
        node << "null";
        return;
    }

    switch (cell->type) {
    case Cell::Type::map: {
        [[fallthrough]];
    }
    case Cell::Type::scope:
    case Cell::Type::vm: {
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
    case Cell::Type::nil:
        node << "null";
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
