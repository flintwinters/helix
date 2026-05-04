#include <core.hpp>

#include <iostream>
#include <string>
#include <memory>

static EvalCellFn evaluate_cell_fn = nullptr;
static RenderShowFn render_show_fn = nullptr;
static MakeErrorFn make_error_fn = nullptr;

static CellPtr make_error(const string& message, CellPtr value = nullptr) {
    if (!make_error_fn) {
        return make_shared<ErrCell>(message, move(value));
    }

    return make_error_fn(message, move(value));
}

static CellPtr evaluate_or_error(CellPtr node, const shared_ptr<MapCell>& root_cell) {
    if (!evaluate_cell_fn) {
        return make_error("builtin evaluation is not initialized");
    }

    CellPtr value = evaluate_cell_fn(move(node), root_cell);
    if (value && value->type == Cell::Type::error_signal) {
        return value;
    }

    return value;
}

static CellPtr evaluate_int_or_error(CellPtr node, const shared_ptr<MapCell>& root_cell, const char* who) {
    return expect_int_cell(evaluate_or_error(move(node), root_cell), who);
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

    CellPtr value = evaluate_or_error(arguments[1], root_cell);
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

    const CellPtr left_cell = evaluate_int_or_error(arguments[1], root_cell, "add");
    if (left_cell && left_cell->type == Cell::Type::error_signal) {
        return left_cell;
    }

    const CellPtr right_cell = evaluate_int_or_error(arguments[2], root_cell, "add");
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

    const CellPtr value = evaluate_or_error(arguments[2], root_cell);
    if (value && value->type == Cell::Type::error_signal) {
        return value;
    }

    const string& name = static_cast<const StrCell&>(*name_cell).value;
    root_cell->set(name, value);
    return value;
}

static CellPtr builtin_eval(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<MapCell> root_cell = expect_map_cell(move(current_vm), "eval");
    if (!root_cell) {
        return make_error("eval requires a map VM");
    }

    CellPtr arity_error = expect_form_arity(arguments.size(), 2, "eval");
    if (arity_error) {
        return arity_error;
    }

    CellPtr code = evaluate_or_error(arguments[1], root_cell);
    if (code && code->type == Cell::Type::error_signal) {
        return code;
    }

    return evaluate_or_error(code, root_cell);
}

static void install_builtin(const shared_ptr<MapCell>& zygote, const string& name, FunCell::Implementation implementation) {
    shared_ptr<FunCell> builtin = make_shared<FunCell>(move(implementation));
    zygote->set(name, builtin);
}

void initialize_builtins(
    EvalCellFn new_evaluate_cell_fn,
    RenderShowFn new_render_show_fn,
    MakeErrorFn new_make_error_fn) {
    evaluate_cell_fn = new_evaluate_cell_fn;
    render_show_fn = new_render_show_fn;
    make_error_fn = new_make_error_fn;
}

shared_ptr<MapCell> make_zygote() {
    shared_ptr<MapCell> zygote = make_shared<MapCell>();
    install_builtin(zygote, "show", builtin_show);
    install_builtin(zygote, "add", builtin_add);
    install_builtin(zygote, "set", builtin_set);
    install_builtin(zygote, "eval", builtin_eval);
    return zygote;
}
