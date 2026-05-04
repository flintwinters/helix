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

static void attach_parent_if_missing(const CellPtr& child, const CellPtr& parent) {
    if (!child || !parent || child->parent) {
        return;
    }

    child->parent = parent;
}

static void set_map_field(const shared_ptr<MapCell>& map_cell, const string& key, CellPtr value) {
    attach_parent_if_missing(value, map_cell);
    map_cell->value[key] = move(value);
}

static bool is_null_cell(ConstCellPtr cell) {
    if (!cell) {
        return true;
    }

    if (cell->type != Cell::Type::string) {
        return false;
    }

    const auto& str_cell = static_cast<const StrCell&>(*cell);
    return str_cell.value == "null";
}

static CellPtr lookup_map_child(const MapCell& map_cell, const string& segment) {
    unordered_map<string, CellPtr>::const_iterator child_it = map_cell.value.find(segment);
    if (child_it == map_cell.value.end()) {
        return nullptr;
    }

    return child_it->second;
}

static ConstCellPtr enclosing_map(ConstCellPtr cell) {
    ConstCellPtr current = cell;
    while (current && current->type != Cell::Type::map) {
        current = current->parent;
    }

    return current;
}

static CellPtr lookup_dotted_name_from(const MapCell& current_map, const string& name, size_t segment_start) {
    size_t dot_index = name.find('.', segment_start);
    string segment = name.substr(segment_start, dot_index - segment_start);
    if (segment.empty()) {
        return nullptr;
    }

    CellPtr current = lookup_map_child(current_map, segment);
    if (!current) {
        return nullptr;
    }

    if (dot_index == string::npos) {
        return current;
    }

    if (current->type != Cell::Type::map) {
        return nullptr;
    }

    return lookup_dotted_name_from(static_cast<const MapCell&>(*current), name, dot_index + 1);
}

static CellPtr lookup_name_in_map(const string& name, const MapCell& map_cell) {
    CellPtr child = lookup_map_child(map_cell, name);
    if (child) {
        return child;
    }

    return lookup_dotted_name_from(map_cell, name, 0);
}

static CellPtr lookup_name_from_context(const string& name, ConstCellPtr context, const shared_ptr<MapCell>& root_cell) {
    ConstCellPtr current = context;
    while (true) {
        ConstCellPtr map_scope = enclosing_map(current);
        if (!map_scope) {
            break;
        }

        CellPtr resolved = lookup_name_in_map(name, static_cast<const MapCell&>(*map_scope));
        if (resolved) {
            return resolved;
        }

        current = map_scope->parent;
    }

    if (!root_cell) {
        return nullptr;
    }

    return lookup_name_in_map(name, *root_cell);
}

static CellPtr evaluate_cell(CellPtr node, const shared_ptr<MapCell>& root_cell);

static CellPtr evaluate_argument(CellPtr node, const shared_ptr<MapCell>& root_cell) {
    CellPtr value = evaluate_cell(move(node), root_cell);
    if (value && value->type == Cell::Type::error_signal) {
        return value;
    }

    return value;
}

static shared_ptr<MapCell> make_finished_state() {
    shared_ptr<MapCell> state = make_shared<MapCell>();
    set_map_field(state, "status", make_shared<StrCell>("finished"));
    set_map_field(state, "frames", make_shared<VecCell>());
    return state;
}

static CellPtr evaluate_form(const VecCell& form, const shared_ptr<MapCell>& root_cell) {
    if (form.value.empty()) {
        return make_error_cell("cannot evaluate an empty vector");
    }

    CellPtr actor = evaluate_argument(form.value.front(), root_cell);
    if (actor && actor->type == Cell::Type::error_signal) {
        return actor;
    }

    if (!actor || !actor->callable) {
        return make_error_cell("vector actor did not resolve to a builtin");
    }

    return actor->call(form.value, root_cell);
}

static CellPtr evaluate_resolved_cell(CellPtr node, CellPtr resolved, const shared_ptr<MapCell>& root_cell) {
    if (!resolved || resolved.get() == node.get()) {
        return node;
    }

    return evaluate_cell(resolved, root_cell);
}

static CellPtr evaluate_cell(CellPtr node, const shared_ptr<MapCell>& root_cell) {
    if (!node) {
        return nullptr;
    }

    if (node->type == Cell::Type::vec) {
        return evaluate_form(static_cast<const VecCell&>(*node), root_cell);
    }

    if (node->type == Cell::Type::string) {
        const auto& name = static_cast<const StrCell&>(*node).value;
        CellPtr resolved = lookup_name_from_context(name, node, root_cell);
        if (resolved) {
            return evaluate_resolved_cell(node, resolved, root_cell);
        }
    }

    return node;
}

static void run_main(shared_ptr<MapCell> root_cell) {
    unordered_map<string, CellPtr>::const_iterator main_it = root_cell->value.find("main");
    if (main_it == root_cell->value.end()) {
        shared_ptr<MapCell> state = make_finished_state();
        set_map_field(state, "result", make_error_cell("program is missing a main entrypoint"));
        set_map_field(root_cell, "state", state);
        return;
    }

    CellPtr result = nullptr;
    if (is_null_cell(main_it->second)) {
        result = main_it->second;
    } else {
        result = evaluate_cell(main_it->second, root_cell);
    }

    shared_ptr<MapCell> state = make_finished_state();
    if (result) {
        set_map_field(state, "result", move(result));
    }
    set_map_field(root_cell, "state", state);
}

int main(int argc, char* argv[]) {
    if (argc != 2) {
        std::cerr << "usage: ./helix <program.yaml>\n";
        return 1;
    }

    try {
        initialize_builtins(evaluate_cell, render_show_output, make_error_cell, set_map_field);
        shared_ptr<MapCell> zygote = make_zygote();
        shared_ptr<MapCell> root_cell = load_root_cell_from_yaml_file(argv[1]);
        attach_parent_if_missing(root_cell, zygote);
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
