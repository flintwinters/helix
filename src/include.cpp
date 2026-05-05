#include <include.hpp>

#include <filesystem>
#include <stdexcept>

#include <ryml_interface.hpp>

using namespace std;

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

void expand_includes_in_root_map(const shared_ptr<MapCell>& root_cell, const filesystem::path& source_path) {
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
        const shared_ptr<MapCell> included_root = load_root_cell_from_yaml_file(include_path.c_str());
        const string binding_name = include_binding_name(include_path);
        root_cell->set(binding_name, included_root);
    }
}
