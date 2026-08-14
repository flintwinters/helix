#include <ryml_interface.hpp>

#include <charconv>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <vector>

#ifndef HELIX_ENABLE_DYNAMIC_LIBRARIES
#define HELIX_ENABLE_DYNAMIC_LIBRARIES 1
#endif

#if HELIX_ENABLE_DYNAMIC_LIBRARIES
#include <dlfcn.h>
#endif

#include <c4/yml/emit.hpp>
#include <c4/yml/event_handler_tree.hpp>
#include <c4/yml/parse.hpp>
#include <c4/yml/parse_engine.hpp>
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

static bool requires_quoted_yaml_scalar(const string& text) {
    if (text.empty() || text == "null") {
        return true;
    }

    int64_t integer_value = 0;
    const char* begin = text.data();
    const char* end = begin + text.size();
    const auto result = from_chars(begin, end, integer_value);
    return result.ec == errc {} && result.ptr == end;
}

static CellPtr scalar_cell_from_ryml(c4::csubstr scalar, bool quoted) {
    const string text = ryml_text_to_string(scalar);
    if (quoted) {
        return make_shared<StrCell>(text);
    }
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

static pair<string, string> split_typed_field_name(const string& field_name) {
    const size_t separator = field_name.find(':');
    if (separator == string::npos) {
        return {field_name, ""};
    }

    if (separator == 0 || separator == field_name.size() - 1 || field_name.find(':', separator + 1) != string::npos) {
        throw runtime_error("typed field names must use name:type syntax");
    }

    return {field_name.substr(0, separator), field_name.substr(separator + 1)};
}

static CellPtr typed_slot_cell_from_sugar(const string& type_name, CellPtr value) {
    SourceLocation source_location = value ? value->source_location : SourceLocation {};
    shared_ptr<MapCell> slot_cell = make_shared<ScopeCell>();
    slot_cell->source_location = move(source_location);
    slot_cell->set(CellField::type, make_shared<StrCell>(type_name));
    slot_cell->set(CellField::value, move(value));
    return slot_cell;
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

static bool is_native_include_path(const filesystem::path& include_path) {
    return include_path.extension() == ".so";
}

#if HELIX_ENABLE_DYNAMIC_LIBRARIES
using NativeModuleInstallFn = CellPtr (*)(const shared_ptr<ScopeCell>&);

static vector<void*> native_library_handles {};

static shared_ptr<ScopeCell> load_native_module_from_library(const filesystem::path& include_path) {
    void* handle = dlopen(include_path.c_str(), RTLD_NOW | RTLD_LOCAL);
    if (!handle) {
        throw runtime_error(string("failed to load native include: ") + dlerror());
    }

    dlerror();
    void* symbol = dlsym(handle, "helix_install_module");
    const char* symbol_error = dlerror();
    if (symbol_error) {
        dlclose(handle);
        throw runtime_error(string("native include is missing helix_install_module: ") + symbol_error);
    }

    auto install_module = reinterpret_cast<NativeModuleInstallFn>(symbol);
    shared_ptr<ScopeCell> module = make_shared<ScopeCell>();
    CellPtr install_result = install_module(module);
    if (is_signal_cell(install_result)) {
        dlclose(handle);
        if (install_result->type == Cell::Type::error_signal) {
            const ErrCell& error = static_cast<const ErrCell&>(*install_result);
            throw runtime_error(error.message);
        }
        throw runtime_error("native include installation returned a signal");
    }

    native_library_handles.push_back(handle);
    return module;
}
#endif

static SourceLocation source_location_from_ryml(c4::yml::ConstNodeRef node, const c4::yml::Parser* parser) {
    if (!parser) {
        return {};
    }

    const c4::yml::Location location = node.location(*parser);
    if (!location || location.line == c4::yml::npos || location.col == c4::yml::npos) {
        return {};
    }

    return {
        ryml_text_to_string(location.name),
        static_cast<int64_t>(location.line + 1),
        static_cast<int64_t>(location.col + 1),
        true,
    };
}

static CellPtr with_source_location(
    CellPtr cell,
    c4::yml::ConstNodeRef node,
    const c4::yml::Parser* parser) {
    if (cell) {
        cell->source_location = source_location_from_ryml(node, parser);
    }
    return cell;
}

static CellPtr cell_from_ryml_node(c4::yml::ConstNodeRef node, const c4::yml::Parser* parser);

CellPtr cell_from_ryml_node(c4::yml::ConstNodeRef node) {
    return cell_from_ryml_node(node, nullptr);
}

[[noreturn]] static void throw_yaml_parse_error(
    c4::csubstr message,
    const c4::yml::ErrorDataParse&,
    void*) {
    throw runtime_error(ryml_text_to_string(message));
}

CellPtr parse_yaml_cell(const string& yaml_text) {
    const c4::csubstr source(yaml_text.data(), yaml_text.size());
    c4::yml::Callbacks callbacks {};
    callbacks.set_error_parse(throw_yaml_parse_error);
    c4::yml::Parser::handler_type event_handler(callbacks);
    c4::yml::Parser parser(&event_handler);
    c4::yml::Tree tree = c4::yml::parse_in_arena(&parser, source);
    c4::yml::ConstNodeRef root = tree.rootref();
    if (root.is_stream() && root.num_children() != 1) {
        throw runtime_error("message YAML must contain exactly one document");
    }
    return cell_from_ryml_node(root);
}

static CellPtr cell_from_ryml_node(c4::yml::ConstNodeRef node, const c4::yml::Parser* parser) {
    while ((node.is_stream() || node.is_doc()) && node.has_children()) {
        node = node.first_child();
    }

    if (node.is_map()) {
        bool has_main = false;
        for (const auto child : node.children()) {
            if (ryml_text_to_string(child.key()) == CellField::main) {
                has_main = true;
                break;
            }
        }

        shared_ptr<MapCell> map_cell = has_main
            ? static_pointer_cast<MapCell>(make_shared<VmCell>())
            : static_pointer_cast<MapCell>(make_shared<ScopeCell>());
        for (const auto child : node.children()) {
            CellPtr child_cell = cell_from_ryml_node(child, parser);
            auto [field_name, type_name] = split_typed_field_name(ryml_text_to_string(child.key()));
            if (!type_name.empty()) {
                child_cell = typed_slot_cell_from_sugar(type_name, move(child_cell));
            }
            map_cell->set(field_name, move(child_cell));
        }
        return with_source_location(map_cell, node, parser);
    }

    if (node.is_seq()) {
        shared_ptr<VecCell> vec_cell = make_shared<VecCell>();
        vec_cell->value.reserve(static_cast<size_t>(node.num_children()));
        for (const auto child : node.children()) {
            CellPtr child_cell = cell_from_ryml_node(child, parser);
            vec_cell->append(move(child_cell));
        }
        return with_source_location(vec_cell, node, parser);
    }

    if (node.has_val()) {
        return with_source_location(scalar_cell_from_ryml(node.val(), node.is_val_quoted()), node, parser);
    }

    return with_source_location(make_shared<StrCell>(), node, parser);
}

static void expand_includes_in_root_map(const shared_ptr<VmCell>& root_cell, const filesystem::path& source_path) {
    if (!root_cell) {
        return;
    }

    unordered_map<string, CellPtr>::const_iterator include_it = root_cell->value.find(CellField::include);
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
        if (is_native_include_path(include_path)) {
#if HELIX_ENABLE_DYNAMIC_LIBRARIES
            root_cell->set(include_binding_name(include_path), load_native_module_from_library(include_path));
#else
            throw runtime_error("native includes require dynamic library support");
#endif
        } else {
            const shared_ptr<VmCell> included_root = load_root_cell_from_yaml_file(include_path.c_str());
            root_cell->set(include_binding_name(include_path), included_root);
        }
    }
}

shared_ptr<VmCell> load_root_cell_from_yaml_file(const char* path) {
    const string file_text = read_yaml_file_text(path);
    const c4::csubstr yaml_text(file_text.data(), file_text.size());
    c4::yml::ParserOptions parser_options {};
    parser_options.locations(true);
    c4::yml::Parser::handler_type event_handler {};
    c4::yml::Parser parser(&event_handler, parser_options);
    c4::yml::Tree tree = c4::yml::parse_in_arena(&parser, path, yaml_text);
    CellPtr root_cell = cell_from_ryml_node(tree.rootref(), &parser);
    shared_ptr<VmCell> root_vm = expect_vm_cell(root_cell);
    if (!root_vm) {
        shared_ptr<MapCell> root_map = expect_map_cell(root_cell);
        if (!root_map) {
            throw runtime_error("top-level YAML document must be a mapping");
        }

        root_vm = make_shared<VmCell>();
        root_vm->source_location = root_map->source_location;
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

static void write_cell_to_ryml_node(ConstCellPtr cell, c4::yml::NodeRef node, bool quote_strings) {
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
            if (quote_strings || requires_quoted_yaml_scalar(key)) {
                child |= c4::yml::KEY_DQUO;
            }
            write_cell_to_ryml_node(value, child, quote_strings);
        }
        return;
    }
    case Cell::Type::vec: {
        node |= c4::yml::SEQ;
        const auto& vec_cell = static_cast<const VecCell&>(*cell);
        for (const CellPtr& value : vec_cell.value) {
            auto child = node.append_child();
            write_cell_to_ryml_node(value, child, quote_strings);
        }
        return;
    }
    case Cell::Type::integer:
        node << static_cast<const IntCell&>(*cell).value;
        return;
    case Cell::Type::string:
        node << static_cast<const StrCell&>(*cell).value;
        if (quote_strings || requires_quoted_yaml_scalar(static_cast<const StrCell&>(*cell).value)) {
            node |= c4::yml::VAL_DQUO;
        }
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
        node[CellField::signal_type] << (cell->type == Cell::Type::return_signal ? CellValue::return_signal : CellValue::signal);
        if (sig_cell.value) {
            write_cell_to_ryml_node(sig_cell.value, node[CellField::value], quote_strings);
        }
        return;
    }
    case Cell::Type::error_signal: {
        node |= c4::yml::MAP;
        const auto& err_cell = static_cast<const ErrCell&>(*cell);
        node[CellField::signal_type] << CellValue::error;
        node[CellField::message] << err_cell.message;
        shared_ptr<MapCell> details = materialize_error_details(err_cell);
        if (details) {
            write_cell_to_ryml_node(details, node[CellField::value], quote_strings);
        } else if (err_cell.value) {
            write_cell_to_ryml_node(err_cell.value, node[CellField::value], quote_strings);
        }
        return;
    }
    case Cell::Type::base:
    default:
        node << "<cell>";
        return;
    }
}

void write_cell_to_ryml_node(ConstCellPtr cell, c4::yml::NodeRef node) {
    write_cell_to_ryml_node(move(cell), node, false);
}

c4::yml::Tree ryml_tree_from_cell(ConstCellPtr root_cell) {
    c4::yml::Tree tree {};
    write_cell_to_ryml_node(root_cell, tree.rootref());
    return tree;
}

string emit_yaml_from_cell(ConstCellPtr root_cell) {
    return c4::yml::emitrs_yaml<string>(ryml_tree_from_cell(root_cell));
}

string emit_round_trip_yaml_from_cell(ConstCellPtr root_cell) {
    c4::yml::Tree tree {};
    write_cell_to_ryml_node(move(root_cell), tree.rootref(), true);
    return c4::yml::emitrs_yaml<string>(tree);
}
