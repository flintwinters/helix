#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>

#include <ryml_interface.hpp>

static string render_show_output(ConstCellPtr cell) {
    string rendered = emit_yaml_from_cell(cell);
    if (cell && (cell->type == Cell::Type::integer || cell->type == Cell::Type::string)) {
        rendered += "...\n";
    }
    return rendered;
}

static CellPtr make_error_cell(const string& message, CellPtr value = nullptr) {
    return make_shared<ErrCell>(message, move(value));
}

static bool is_error_cell(ConstCellPtr cell) {
    return cell && cell->type == Cell::Type::error_signal;
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

static CellPtr expect_int_cell(ConstCellPtr cell, const char* who) {
    if (is_error_cell(cell)) {
        return const_pointer_cast<Cell>(cell);
    }

    if (!cell || cell->type != Cell::Type::integer) {
        return make_error_cell(string(who) + " expects integer arguments");
    }

    return const_pointer_cast<Cell>(cell);
}

static CellPtr evaluate_cell(CellPtr node, MapCell& root);

static CellPtr evaluate_show_form(const VecCell& form, MapCell& root) {
    if (form.value.size() != 2) {
        return make_error_cell("show expects exactly 1 argument");
    }

    const CellPtr value = evaluate_cell(form.value[1], root);
    if (is_error_cell(value)) {
        return value;
    }

    cout << render_show_output(value);
    return nullptr;
}

static CellPtr evaluate_add_form(const VecCell& form, MapCell& root) {
    if (form.value.size() != 3) {
        return make_error_cell("add expects exactly 2 arguments");
    }

    const CellPtr left_cell = expect_int_cell(evaluate_cell(form.value[1], root), "add");
    if (is_error_cell(left_cell)) {
        return left_cell;
    }

    const CellPtr right_cell = expect_int_cell(evaluate_cell(form.value[2], root), "add");
    if (is_error_cell(right_cell)) {
        return right_cell;
    }

    const int64_t left = static_cast<const IntCell&>(*left_cell).value;
    const int64_t right = static_cast<const IntCell&>(*right_cell).value;
    return make_shared<IntCell>(left + right);
}

static CellPtr evaluate_set_form(const VecCell& form, MapCell& root) {
    if (form.value.size() != 3) {
        return make_error_cell("set expects exactly 2 arguments");
    }

    const CellPtr name_cell = form.value[1];
    if (!name_cell || name_cell->type != Cell::Type::string) {
        return make_error_cell("set expects a string name");
    }

    const CellPtr value = evaluate_cell(form.value[2], root);
    if (is_error_cell(value)) {
        return value;
    }

    const string& name = static_cast<const StrCell&>(*name_cell).value;
    root.value[name] = value;
    return value;
}

static CellPtr evaluate_builtin_form(const string& actor_name, const VecCell& form, MapCell& root) {
    if (actor_name == "show") {
        return evaluate_show_form(form, root);
    }

    if (actor_name == "add") {
        return evaluate_add_form(form, root);
    }

    if (actor_name == "set") {
        return evaluate_set_form(form, root);
    }

    return make_error_cell("vector actor did not resolve to a builtin");
}

static CellPtr evaluate_form(const VecCell& form, MapCell& root) {
    if (form.value.empty()) {
        return make_error_cell("cannot evaluate an empty vector");
    }

    const CellPtr actor = form.value.front();
    if (!actor || actor->type != Cell::Type::string) {
        return make_error_cell("vector actor did not resolve to a builtin");
    }

    const auto& actor_name = static_cast<const StrCell&>(*actor).value;
    return evaluate_builtin_form(actor_name, form, root);
}

static CellPtr evaluate_cell(CellPtr node, MapCell& root) {
    if (!node) {
        return nullptr;
    }

    if (node->type == Cell::Type::vec) {
        return evaluate_form(static_cast<const VecCell&>(*node), root);
    }

    if (node->type == Cell::Type::string) {
        const auto& name = static_cast<const StrCell&>(*node).value;
        const auto found = root.value.find(name);
        if (found != root.value.end()) {
            return found->second;
        }
    }

    return node;
}

static void run_main(shared_ptr<MapCell> root_cell) {
    MapCell& root = *root_cell;
    unordered_map<string, CellPtr>::const_iterator main_it = root.value.find("main");
    if (main_it == root.value.end()) {
        root.value["state"] = make_shared<MapCell>(
            unordered_map<string, CellPtr> {
                {"status", make_shared<StrCell>("finished")},
                {"frames", make_shared<VecCell>()},
                {"result", make_error_cell("program is missing a main entrypoint")},
            });
        return;
    }

    CellPtr result = nullptr;
    if (is_null_cell(main_it->second)) {
        result = main_it->second;
    } else {
        result = evaluate_cell(main_it->second, root);
    }

    shared_ptr<MapCell> state = make_shared<MapCell>();
    state->value["status"] = make_shared<StrCell>("finished");
    state->value["frames"] = make_shared<VecCell>();
    if (result) {
        state->value["result"] = move(result);
    }
    root.value["state"] = move(state);
}

int main(int argc, char* argv[]) {
    if (argc != 2) {
        std::cerr << "usage: ./helix <program.yaml>\n";
        return 1;
    }

    try {
        shared_ptr<MapCell> root_cell = load_root_cell_from_yaml_file(argv[1]);
        run_main(root_cell);
        std::cout << emit_yaml_from_cell(root_cell);
    } catch (const exception& error) {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }

    return 0;
}
