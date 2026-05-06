#include <iostream>
#include <memory>
#include <string>

#include <ryml_interface.hpp>

static string render_show_output(ConstCellPtr cell) {
    string rendered = emit_yaml_from_cell(cell);
    if (cell && (cell->type == Cell::Type::integer || cell->type == Cell::Type::string)) {
        rendered += "...\n";
    }
    return rendered;
}

static bool is_null_cell(ConstCellPtr cell) {
    if (!cell) {
        return true;
    }

    return cell->type == Cell::Type::nil;
}

static CellPtr resolve_dotted_name_in_map(const string& name, const MapCell& map_cell) {
    const MapCell* current_map = &map_cell;
    size_t segment_start = 0;

    while (segment_start < name.size()) {
        size_t dot_index = name.find('.', segment_start);
        string segment = name.substr(segment_start, dot_index - segment_start);
        if (segment.empty()) {
            return nullptr;
        }

        unordered_map<string, CellPtr>::const_iterator child_it = current_map->value.find(segment);
        if (child_it == current_map->value.end()) {
            return nullptr;
        }

        CellPtr current = child_it->second;
        if (dot_index == string::npos) {
            return current;
        }

        if (!current || current->type != Cell::Type::map) {
            return nullptr;
        }

        current_map = &static_cast<const MapCell&>(*current);
        segment_start = dot_index + 1;
    }

    return nullptr;
}

static CellPtr resolve_name_in_map(const string& name, const MapCell& map_cell) {
    unordered_map<string, CellPtr>::const_iterator exact_it = map_cell.value.find(name);
    if (exact_it != map_cell.value.end()) {
        return exact_it->second;
    }

    return resolve_dotted_name_in_map(name, map_cell);
}

static CellPtr resolve_name_from_context(const string& name, ConstCellPtr context, const shared_ptr<MapCell>& root_cell) {
    ConstCellPtr current = context;

    while (current) {
        while (current && current->type != Cell::Type::map) {
            current = current->parent;
        }

        if (!current) {
            break;
        }

        CellPtr resolved = resolve_name_in_map(name, static_cast<const MapCell&>(*current));
        if (resolved) {
            return resolved;
        }

        current = current->parent;
    }

    if (!root_cell) {
        return nullptr;
    }

    return resolve_name_in_map(name, *root_cell);
}

static CellPtr evaluate_cell(CellPtr node, const shared_ptr<MapCell>& root_cell);

static CellPtr resolve_cell(CellPtr node, const shared_ptr<MapCell>& root_cell) {
    if (!node) {
        return nullptr;
    }

    if (node->type != Cell::Type::string) {
        return node;
    }

    const auto& name = static_cast<const StrCell&>(*node).value;
    CellPtr resolved = resolve_name_from_context(name, node, root_cell);
    if (resolved) {
        return resolved;
    }

    return node;
}

static CellPtr evaluate_form(const VecCell& form, const shared_ptr<MapCell>& root_cell) {
    if (form.value.empty()) {
        return make_error_cell("cannot evaluate an empty vector");
    }

    CellPtr actor = evaluate_cell(form.value.front(), root_cell);
    if (actor && actor->type == Cell::Type::error_signal) {
        return actor;
    }

    if (!actor || actor->type != Cell::Type::function) {
        return make_error_cell("vector actor did not resolve to a builtin");
    }

    return actor->call(form.value, root_cell);
}

static CellPtr evaluate_cell(CellPtr node, const shared_ptr<MapCell>& root_cell) {
    if (!node) {
        return nullptr;
    }

    if (node->type == Cell::Type::vec) {
        return evaluate_form(static_cast<const VecCell&>(*node), root_cell);
    }

    if (node->type == Cell::Type::string) {
        CellPtr resolved = resolve_cell(node, root_cell);
        if (resolved && resolved.get() != node.get()) {
            return evaluate_cell(resolved, root_cell);
        }
    }

    return node;
}

static void run_main(shared_ptr<MapCell> root_cell) {
    unordered_map<string, CellPtr>::const_iterator main_it = root_cell->value.find("main");
    if (main_it == root_cell->value.end()) {
        attach_finished_state(root_cell, make_error_cell("program is missing a main entrypoint"));
        return;
    }

    CellPtr result = nullptr;
    if (is_null_cell(main_it->second)) {
        result = main_it->second;
    } else {
        result = evaluate_cell(main_it->second, root_cell);
    }

    attach_finished_state(root_cell, move(result));
}

int main(int argc, char* argv[]) {
    if (argc != 2) {
        std::cerr << "usage: ./helix <program.yaml>\n";
        return 1;
    }

    try {
        initialize_builtins(evaluate_cell, resolve_cell, render_show_output, make_error_cell);
        shared_ptr<MapCell> zygote = make_zygote();
        shared_ptr<MapCell> root_cell = load_root_cell_from_yaml_file(argv[1]);
        if (!root_cell->parent) {
            root_cell->parent = zygote;
        }
        run_main(root_cell);
        string yaml_output = emit_yaml_from_cell(root_cell);
        root_cell->clear_descendant_parent_links();
        root_cell->parent = nullptr;
        zygote->clear_descendant_parent_links();
        std::cout << yaml_output;
    } catch (const exception& error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }

    return 0;
}
