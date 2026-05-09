#include <core.hpp>

#include <iostream>
#include <string>
#include <memory>

static EvalCellFn evaluate_cell_fn = nullptr;
static ResolveCellFn resolve_cell_fn = nullptr;
static AdvanceVmFn advance_vm_fn = nullptr;
static RenderShowFn render_show_fn = nullptr;
static MakeErrorFn make_error_fn = nullptr;
using VmCallbackFn = CellPtr(*)(CellPtr, const shared_ptr<MapCell>&);

static CellPtr make_error(const string& message, CellPtr value = nullptr) {
    if (!make_error_fn) {
        return make_shared<ErrCell>(message, move(value));
    }

    return make_error_fn(message, move(value));
}

static CellPtr apply_vm_callback_or_signal(
    VmCallbackFn callback,
    CellPtr node,
    const shared_ptr<MapCell>& root_cell,
    const char* init_error_message) {
    if (!callback) {
        return make_error(init_error_message);
    }

    CellPtr value = callback(move(node), root_cell);
    if (is_signal_cell(value)) {
        return value;
    }

    return value;
}

static CellPtr evaluate_or_signal(CellPtr node, const shared_ptr<MapCell>& root_cell) {
    return apply_vm_callback_or_signal(
        evaluate_cell_fn,
        move(node),
        root_cell,
        "builtin evaluation is not initialized");
}

static CellPtr evaluate_int_or_error(CellPtr node, const shared_ptr<MapCell>& root_cell, const char* who) {
    return expect_int_cell(evaluate_or_signal(move(node), root_cell), who);
}

static CellPtr resolve_or_signal(CellPtr node, const shared_ptr<MapCell>& root_cell) {
    return apply_vm_callback_or_signal(
        resolve_cell_fn,
        move(node),
        root_cell,
        "builtin resolution is not initialized");
}

static bool is_truthy(ConstCellPtr cell) {
    if (!cell || cell->type == Cell::Type::nil) {
        return false;
    }

    if (cell->type == Cell::Type::integer) {
        return static_cast<const IntCell&>(*cell).value != 0;
    }

    return true;
}

static shared_ptr<MapCell> expect_vm_state(const shared_ptr<MapCell>& vm) {
    unordered_map<string, CellPtr>::const_iterator state_it = vm->value.find("state");
    if (state_it == vm->value.end() || !state_it->second || state_it->second->type != Cell::Type::map) {
        return nullptr;
    }

    return static_pointer_cast<MapCell>(state_it->second);
}

static shared_ptr<MapCell> expect_builtin_vm(const vector<CellPtr>& arguments, CellPtr current_vm, const char* who) {
    shared_ptr<MapCell> root_cell = expect_map_cell(move(current_vm), who);
    if (!root_cell) {
        return nullptr;
    }

    CellPtr arity_error = expect_form_arity(arguments.size(), 2, who);
    if (arity_error) {
        return nullptr;
    }

    return root_cell;
}

static shared_ptr<MapCell> expect_builtin_vm(
    const vector<CellPtr>& arguments,
    CellPtr current_vm,
    const char* who,
    size_t expected_arity) {
    shared_ptr<MapCell> root_cell = expect_map_cell(move(current_vm), who);
    if (!root_cell) {
        return nullptr;
    }

    CellPtr arity_error = expect_form_arity(arguments.size(), expected_arity, who);
    if (arity_error) {
        return nullptr;
    }

    return root_cell;
}

static const string* vm_status_string(const shared_ptr<MapCell>& vm) {
    shared_ptr<MapCell> state = expect_vm_state(vm);
    if (!state) {
        return nullptr;
    }

    unordered_map<string, CellPtr>::const_iterator status_it = state->value.find("status");
    if (status_it == state->value.end() || !status_it->second || status_it->second->type != Cell::Type::string) {
        return nullptr;
    }

    return &static_cast<const StrCell&>(*status_it->second).value;
}

static CellPtr vm_status_cell(const shared_ptr<MapCell>& vm) {
    shared_ptr<MapCell> state = expect_vm_state(vm);
    if (!state) {
        return nullptr;
    }

    unordered_map<string, CellPtr>::const_iterator status_it = state->value.find("status");
    if (status_it == state->value.end() || !status_it->second || status_it->second->type != Cell::Type::string) {
        return nullptr;
    }

    return status_it->second;
}

static void arm_list_frame(const shared_ptr<MapCell>& vm, CellPtr sequence_cell) {
    shared_ptr<MapCell> state = expect_vm_state(vm);
    shared_ptr<MapCell> frame = make_shared<MapCell>();
    frame->set("name", make_shared<StrCell>("list"));
    frame->set("values", move(sequence_cell));
    frame->set("index", make_shared<IntCell>(0));
    state->set("frames", make_shared<VecCell>(vector<CellPtr> {frame}));
    state->set("status", make_shared<StrCell>("running"));
    state->value.erase("result");
    state->value.erase("error");
}

static shared_ptr<MapCell> resolve_child_vm(const vector<CellPtr>& arguments, CellPtr current_vm, const char* who) {
    shared_ptr<MapCell> root_cell = expect_builtin_vm(arguments, move(current_vm), who);
    if (!root_cell) {
        return nullptr;
    }

    const CellPtr child_name = arguments[1];
    if (!child_name || child_name->type != Cell::Type::string) {
        return nullptr;
    }

    if (!resolve_cell_fn) {
        return nullptr;
    }

    CellPtr resolved = resolve_cell_fn(child_name, root_cell);
    shared_ptr<MapCell> child_vm = expect_map_cell(move(resolved), who);
    if (!child_vm) {
        return nullptr;
    }

    child_vm->parent = root_cell->parent;
    return child_vm;
}

static CellPtr builtin_show(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<MapCell> root_cell = expect_builtin_vm(arguments, move(current_vm), "show");
    if (!root_cell) {
        return make_error("show requires a map VM");
    }

    CellPtr value = evaluate_or_signal(arguments[1], root_cell);
    if (is_signal_cell(value)) {
        return value;
    }

    if (render_show_fn) {
        cout << render_show_fn(value);
    }
    return nullptr;
}

static CellPtr builtin_add(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<MapCell> root_cell = expect_builtin_vm(arguments, move(current_vm), "add", 3);
    if (!root_cell) {
        return make_error("add requires a map VM");
    }

    const CellPtr left_cell = evaluate_int_or_error(arguments[1], root_cell, "add");
    if (is_signal_cell(left_cell)) {
        return left_cell;
    }

    const CellPtr right_cell = evaluate_int_or_error(arguments[2], root_cell, "add");
    if (is_signal_cell(right_cell)) {
        return right_cell;
    }

    const int64_t left = static_cast<const IntCell&>(*left_cell).value;
    const int64_t right = static_cast<const IntCell&>(*right_cell).value;
    return make_shared<IntCell>(left + right);
}

static CellPtr builtin_set(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<MapCell> root_cell = expect_builtin_vm(arguments, move(current_vm), "set", 3);
    if (!root_cell) {
        return make_error("set requires a map VM");
    }

    const CellPtr name_cell = arguments[1];
    if (!name_cell || name_cell->type != Cell::Type::string) {
        return make_error("set expects a string name");
    }

    const CellPtr value = evaluate_or_signal(arguments[2], root_cell);
    if (is_signal_cell(value)) {
        return value;
    }

    const string& name = static_cast<const StrCell&>(*name_cell).value;
    root_cell->set(name, value);
    return value;
}

static CellPtr builtin_eval(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<MapCell> root_cell = expect_builtin_vm(arguments, move(current_vm), "eval");
    if (!root_cell) {
        return make_error("eval requires a map VM");
    }

    CellPtr code = evaluate_or_signal(arguments[1], root_cell);
    if (is_signal_cell(code)) {
        return code;
    }

    return evaluate_or_signal(code, root_cell);
}

static CellPtr builtin_list(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<MapCell> root_cell = expect_builtin_vm(arguments, move(current_vm), "list");
    if (!root_cell) {
        return make_error("list requires a map VM");
    }

    CellPtr sequence_cell = resolve_or_signal(arguments[1], root_cell);
    if (is_signal_cell(sequence_cell)) {
        return sequence_cell;
    }

    if (!sequence_cell || sequence_cell->type != Cell::Type::vec) {
        return make_error("list expects a vector sequence");
    }

    if (!expect_vm_state(root_cell)) {
        return make_error("list requires VM state");
    }

    arm_list_frame(root_cell, sequence_cell);
    return make_shared<NilCell>();
}

static CellPtr builtin_if(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<MapCell> root_cell = expect_builtin_vm(arguments, move(current_vm), "if", 4);
    if (!root_cell) {
        return make_error("if requires a map VM");
    }

    CellPtr condition = evaluate_or_signal(arguments[1], root_cell);
    if (is_signal_cell(condition)) {
        return condition;
    }

    if (is_truthy(condition)) {
        return evaluate_or_signal(arguments[2], root_cell);
    }

    return evaluate_or_signal(arguments[3], root_cell);
}

static CellPtr builtin_while(const vector<CellPtr>& arguments, CellPtr current_vm) {
    shared_ptr<MapCell> root_cell = expect_builtin_vm(arguments, move(current_vm), "while", 3);
    if (!root_cell) {
        return make_error("while requires a map VM");
    }

    CellPtr body_cell = resolve_or_signal(arguments[2], root_cell);
    if (is_signal_cell(body_cell)) {
        return body_cell;
    }

    if (!body_cell || body_cell->type != Cell::Type::vec) {
        return make_error("while expects a vector body");
    }

    const VecCell& body = static_cast<const VecCell&>(*body_cell);
    while (true) {
        CellPtr condition = evaluate_or_signal(arguments[1], root_cell);
        if (is_signal_cell(condition)) {
            return condition;
        }

        if (!is_truthy(condition)) {
            return make_shared<NilCell>();
        }

        for (const CellPtr& element : body.value) {
            CellPtr value = evaluate_or_signal(element, root_cell);
            if (is_signal_cell(value)) {
                return value;
            }
        }
    }
}

static CellPtr builtin_start(const vector<CellPtr>& arguments, CellPtr current_vm) {
    if (!advance_vm_fn) {
        return make_error("start is not initialized");
    }

    shared_ptr<MapCell> child_vm = resolve_child_vm(arguments, move(current_vm), "start");
    if (!child_vm) {
        return make_error("start expects a child VM name");
    }

    while (true) {
        CellPtr step_result = advance_vm_fn(child_vm);
        if (is_signal_cell(step_result)) {
            return step_result;
        }

        const string* status = vm_status_string(child_vm);
        if (!status) {
            return make_error("start requires child VM state");
        }

        if (*status == "finished" || *status == "error" || *status == "signaled") {
            return step_result;
        }
    }
}

static CellPtr builtin_step(const vector<CellPtr>& arguments, CellPtr current_vm) {
    if (!advance_vm_fn) {
        return make_error("step is not initialized");
    }

    shared_ptr<MapCell> child_vm = resolve_child_vm(arguments, move(current_vm), "step");
    if (!child_vm) {
        return make_error("step expects a child VM name");
    }

    advance_vm_fn(child_vm);
    CellPtr status_cell = vm_status_cell(child_vm);
    if (!status_cell) {
        return make_error("step requires child VM state");
    }

    return status_cell;
}

static void install_builtin(const shared_ptr<MapCell>& zygote, const string& name, FunCell::Implementation implementation) {
    shared_ptr<FunCell> builtin = make_shared<FunCell>(move(implementation));
    zygote->set(name, builtin);
}

void initialize_builtins(
    EvalCellFn new_evaluate_cell_fn,
    ResolveCellFn new_resolve_cell_fn,
    AdvanceVmFn new_advance_vm_fn,
    RenderShowFn new_render_show_fn,
    MakeErrorFn new_make_error_fn) {
    evaluate_cell_fn = new_evaluate_cell_fn;
    resolve_cell_fn = new_resolve_cell_fn;
    advance_vm_fn = new_advance_vm_fn;
    render_show_fn = new_render_show_fn;
    make_error_fn = new_make_error_fn;
}

shared_ptr<MapCell> make_zygote() {
    shared_ptr<MapCell> zygote = make_shared<MapCell>();
    install_builtin(zygote, "show", builtin_show);
    install_builtin(zygote, "add", builtin_add);
    install_builtin(zygote, "set", builtin_set);
    install_builtin(zygote, "eval", builtin_eval);
    install_builtin(zygote, "list", builtin_list);
    install_builtin(zygote, "if", builtin_if);
    install_builtin(zygote, "while", builtin_while);
    install_builtin(zygote, "start", builtin_start);
    install_builtin(zygote, "step", builtin_step);
    return zygote;
}
