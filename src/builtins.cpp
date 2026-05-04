#include <core.hpp>

#include <iostream>
#include <string>
#include <memory>

static EvalCellFn evaluate_cell_fn = nullptr;
static RenderShowFn render_show_fn = nullptr;
static MakeErrorFn make_error_fn = nullptr;
static SetMapFieldFn set_map_field_fn = nullptr;

static CellPtr make_error(const string& message, CellPtr value = nullptr) {
    if (!make_error_fn) {
        return make_shared<ErrCell>(message, move(value));
    }

    return make_error_fn(message, move(value));
}

static CellPtr builtin_show(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<MapCell> root_cell = expect_map_cell(move(current_vm), "show");
    if (!root_cell) {
        return make_error("show requires a map VM");
    }

    CellPtr arity_error = expect_form_arity(arguments.size(), 2, "show");
    if (arity_error) {
        return arity_error;
    }

    if (!evaluate_cell_fn) {
        return make_error("builtin evaluation is not initialized");
    }

    CellPtr value = evaluate_cell_fn(arguments[1], root_cell);
    if (value && value->type == Cell::Type::error_signal) {
        return value;
    }

    if (render_show_fn) {
        cout << render_show_fn(value);
    }
    return nullptr;
}

static CellPtr builtin_add(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<MapCell> root_cell = expect_map_cell(move(current_vm), "add");
    if (!root_cell) {
        return make_error("add requires a map VM");
    }

    CellPtr arity_error = expect_form_arity(arguments.size(), 3, "add");
    if (arity_error) {
        return arity_error;
    }

    if (!evaluate_cell_fn) {
        return make_error("builtin evaluation is not initialized");
    }

    const CellPtr left_cell = expect_int_cell(evaluate_cell_fn(arguments[1], root_cell), "add");
    if (left_cell && left_cell->type == Cell::Type::error_signal) {
        return left_cell;
    }

    const CellPtr right_cell = expect_int_cell(evaluate_cell_fn(arguments[2], root_cell), "add");
    if (right_cell && right_cell->type == Cell::Type::error_signal) {
        return right_cell;
    }

    const int64_t left = static_cast<const IntCell&>(*left_cell).value;
    const int64_t right = static_cast<const IntCell&>(*right_cell).value;
    return make_shared<IntCell>(left + right);
}

static CellPtr builtin_set(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<MapCell> root_cell = expect_map_cell(move(current_vm), "set");
    if (!root_cell) {
        return make_error("set requires a map VM");
    }

    CellPtr arity_error = expect_form_arity(arguments.size(), 3, "set");
    if (arity_error) {
        return arity_error;
    }

    const CellPtr name_cell = arguments[1];
    if (!name_cell || name_cell->type != Cell::Type::string) {
        return make_error("set expects a string name");
    }

    if (!evaluate_cell_fn) {
        return make_error("builtin evaluation is not initialized");
    }

    const CellPtr value = evaluate_cell_fn(arguments[2], root_cell);
    if (value && value->type == Cell::Type::error_signal) {
        return value;
    }

    if (!set_map_field_fn) {
        return make_error("set map helper is not initialized");
    }

    const string& name = static_cast<const StrCell&>(*name_cell).value;
    set_map_field_fn(root_cell, name, value);
    return value;
}

static void install_builtin(const shared_ptr<MapCell>& zygote, const string& name, FunCell::Implementation implementation) {
    shared_ptr<FunCell> builtin = make_shared<FunCell>(move(implementation));
    set_map_field_fn(zygote, name, builtin);
}

void initialize_builtins(
    EvalCellFn new_evaluate_cell_fn,
    RenderShowFn new_render_show_fn,
    MakeErrorFn new_make_error_fn,
    SetMapFieldFn new_set_map_field_fn) {
    evaluate_cell_fn = new_evaluate_cell_fn;
    render_show_fn = new_render_show_fn;
    make_error_fn = new_make_error_fn;
    set_map_field_fn = new_set_map_field_fn;
}

shared_ptr<MapCell> make_zygote() {
    shared_ptr<MapCell> zygote = make_shared<MapCell>();
    install_builtin(zygote, "show", builtin_show);
    install_builtin(zygote, "add", builtin_add);
    install_builtin(zygote, "set", builtin_set);
    return zygote;
}
