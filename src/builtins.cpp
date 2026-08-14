#include <core.hpp>

#include <iostream>
#include <memory>
#include <string>

static EvalCellFn evaluate_cell_fn = nullptr;
static ResolveCellFn resolve_cell_fn = nullptr;
static AdvanceVmFn advance_vm_fn = nullptr;
static RenderShowFn render_show_fn = nullptr;
static MakeErrorFn make_error_fn = nullptr;
using VmCallbackFn = CellPtr(*)(CellPtr, const shared_ptr<VmCell>&);
using IntBinaryOperation = int64_t(*)(int64_t, int64_t);

struct BuiltinVmValidation {
    shared_ptr<VmCell> vm {};
    CellPtr error {};
};

static CellPtr make_error_with_origin(const string& message, CellPtr value, CppErrorOrigin origin);
static CellPtr make_error_with_origin(const string& message, ErrorDetails details, CppErrorOrigin origin);

static CellPtr make_error_with_origin(const string& message, CppErrorOrigin origin) {
    return make_error_with_origin(message, nullptr, origin);
}

static CellPtr make_error_with_origin(const string& message, CellPtr value, CppErrorOrigin origin) {
    if (!make_error_fn) {
        return make_error_cell_at(message, move(value), origin);
    }

    return make_error_fn(message, move(value), origin);
}

static CellPtr make_error_with_origin(const string& message, ErrorDetails details, CppErrorOrigin origin) {
    if (!make_error_fn) {
        return make_error_cell_at(message, move(details), origin);
    }

    CellPtr error = make_error_fn(message, nullptr, origin);
    attach_error_details(error, move(details));
    return error;
}

#define make_error(message, ...) make_error_with_origin((message) __VA_OPT__(,) __VA_ARGS__, HELIX_CPP_ERROR_ORIGIN)

static CellPtr make_error_at(const string& message, ConstCellPtr source, CellPtr value = nullptr) {
    CellPtr error = make_error(message, move(value));
    attach_error_location(error, move(source));
    return error;
}

static CellPtr apply_vm_callback_or_signal(
    VmCallbackFn callback,
    CellPtr node,
    const shared_ptr<VmCell>& root_cell,
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

static CellPtr evaluate_or_signal(CellPtr node, const shared_ptr<VmCell>& root_cell) {
    return apply_vm_callback_or_signal(
        evaluate_cell_fn,
        move(node),
        root_cell,
        "builtin evaluation is not initialized");
}

static CellPtr clone_cell_tree(ConstCellPtr cell);

static shared_ptr<VecCell> clone_vec_cell(const VecCell& cell) {
    shared_ptr<VecCell> copy = make_shared<VecCell>();
    for (const CellPtr& element : cell.value) {
        copy->append(clone_cell_tree(element));
    }
    return copy;
}

static shared_ptr<MapCell> clone_map_like_cell(ConstCellPtr cell) {
    shared_ptr<MapCell> copy = cell->type == Cell::Type::vm
        ? static_pointer_cast<MapCell>(make_shared<VmCell>())
        : cell->type == Cell::Type::scope
            ? static_pointer_cast<MapCell>(make_shared<ScopeCell>())
            : make_shared<MapCell>();
    const MapCell& source = static_cast<const MapCell&>(*cell);
    for (const auto& [key, value] : source.value) {
        copy->set(key, clone_cell_tree(value));
    }
    return copy;
}

static CellPtr clone_cell_tree(ConstCellPtr cell) {
    if (!cell) {
        return nullptr;
    }

    switch (cell->type) {
    case Cell::Type::map:
    case Cell::Type::scope:
    case Cell::Type::vm:
        return clone_map_like_cell(cell);
    case Cell::Type::vec:
        return clone_vec_cell(static_cast<const VecCell&>(*cell));
    case Cell::Type::integer:
        return make_shared<IntCell>(static_cast<const IntCell&>(*cell).value);
    case Cell::Type::string:
        return make_shared<StrCell>(static_cast<const StrCell&>(*cell).value);
    case Cell::Type::nil:
        return make_shared<NilCell>();
    case Cell::Type::return_signal:
        return make_shared<RetCell>(clone_cell_tree(static_cast<const SigCell&>(*cell).value));
    case Cell::Type::error_signal: {
        const ErrCell& error = static_cast<const ErrCell&>(*cell);
        shared_ptr<ErrCell> copy = make_shared<ErrCell>(error.message, clone_cell_tree(error.value));
        copy->details = error.details;
        return copy;
    }
    case Cell::Type::signal:
        return make_shared<SigCell>(clone_cell_tree(static_cast<const SigCell&>(*cell).value));
    case Cell::Type::function:
    case Cell::Type::base:
        return const_pointer_cast<Cell>(cell);
    }

    return const_pointer_cast<Cell>(cell);
}

static CellPtr evaluate_int_or_error(CellPtr node, const shared_ptr<VmCell>& root_cell, const char* who) {
    CellPtr source = node;
    CellPtr result = expect_int_cell(evaluate_or_signal(move(node), root_cell), who);
    if (result && result->type == Cell::Type::error_signal) {
        attach_error_location(result, source);
    }
    return result;
}

static CellPtr vec_or_signal_from_callback(
    VmCallbackFn callback,
    CellPtr node,
    const shared_ptr<VmCell>& root_cell,
    const string& init_error_message,
    const string& type_error_message) {
    CellPtr source = node;
    CellPtr value = apply_vm_callback_or_signal(
        callback,
        move(node),
        root_cell,
        init_error_message.c_str());
    if (is_signal_cell(value)) {
        return value;
    }

    if (!value || value->type != Cell::Type::vec) {
        return make_error_at(type_error_message, source ? source : value);
    }

    return value;
}

static CellPtr resolve_or_signal(CellPtr node, const shared_ptr<VmCell>& root_cell) {
    CellPtr original = node;
    CellPtr value = apply_vm_callback_or_signal(
        resolve_cell_fn,
        move(node),
        root_cell,
        "builtin resolution is not initialized");
    if (is_signal_cell(value)) {
        return value;
    }

    if (value && original && value.get() == original.get() && value->type == Cell::Type::string) {
        ErrorDetails details;
        details.fields[CellField::type] = make_string_error_detail(CellValue::unresolved_name);
        details.fields[CellField::name] = make_string_error_detail(static_cast<const StrCell&>(*value).value);
        details.fields[CellField::context_type] = make_string_error_detail(cell_class_name(root_cell));
        attach_source_location(details, value);
        return make_error("builtin resolution failed", move(details));
    }

    return value;
}

static shared_ptr<MapCell> nearest_scope_like(CellPtr node, const shared_ptr<VmCell>& root_cell) {
    for (CellPtr current = move(node); current; current = current->parent) {
        if (is_map_like_cell(current)) {
            return static_pointer_cast<MapCell>(current);
        }
    }

    return root_cell;
}

static CellPtr resolve_name_like(CellPtr name_cell, const shared_ptr<VmCell>& root_cell) {
    if (!resolve_cell_fn) {
        return make_error("builtin resolution is not initialized");
    }

    return resolve_cell_fn(move(name_cell), root_cell);
}

static CellPtr assign_field_or_slot(const shared_ptr<MapCell>& receiver, const string& key, CellPtr value) {
    if (!receiver) {
        return make_error("set target must resolve to a map-like cell");
    }

    CellPtr existing = map_field_cell(receiver, key);
    if (is_typed_slot(existing)) {
        return set_typed_slot_value(static_pointer_cast<MapCell>(existing), move(value));
    }

    receiver->set(key, move(value));
    return nullptr;
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

static BuiltinVmValidation expect_builtin_vm(
    const vector<CellPtr>& arguments,
    CellPtr current_vm,
    const char* who,
    size_t expected_arity) {
    shared_ptr<VmCell> root_cell = expect_vm_cell(move(current_vm));
    if (!root_cell) {
        return {nullptr, make_error(string(who) + " requires a map VM")};
    }

    CellPtr arity_error = expect_form_arity(arguments.size(), expected_arity, who);
    if (arity_error) {
        attach_error_location(arity_error, arguments.empty() ? nullptr : arguments.front());
        return {nullptr, arity_error};
    }

    return {root_cell, nullptr};
}

static BuiltinVmValidation expect_builtin_vm(
    const vector<CellPtr>& arguments,
    CellPtr current_vm,
    const char* who) {
    return expect_builtin_vm(arguments, move(current_vm), who, 2);
}

static BuiltinVmValidation resolve_child_vm(const vector<CellPtr>& arguments, CellPtr current_vm, const char* who) {
    BuiltinVmValidation validation = expect_builtin_vm(arguments, move(current_vm), who);
    if (validation.error) {
        return validation;
    }

    const CellPtr child_name = arguments[1];
    if (!child_name || child_name->type != Cell::Type::string) {
        return {nullptr, make_error_at(string(who) + " expects a child VM name", child_name)};
    }

    if (!resolve_cell_fn) {
        return {nullptr, make_error("builtin resolution is not initialized")};
    }

    CellPtr resolved = resolve_cell_fn(child_name, validation.vm);
    if (is_signal_cell(resolved)) {
        return {nullptr, resolved};
    }

    shared_ptr<VmCell> child_vm = expect_vm_cell(move(resolved));
    if (!child_vm) {
        return {nullptr, make_error_at(string(who) + " expects a child VM name", child_name)};
    }

    return {child_vm, nullptr};
}

static CellPtr resolve_vec_or_signal(
    CellPtr node,
    const shared_ptr<VmCell>& root_cell,
    const string& type_error_message) {
    return vec_or_signal_from_callback(
        resolve_cell_fn,
        move(node),
        root_cell,
        "builtin resolution is not initialized",
        type_error_message);
}

static CellPtr resolve_vec_target_or_signal(
    CellPtr node,
    const shared_ptr<VmCell>& root_cell,
    const char* who) {
    return resolve_vec_or_signal(move(node), root_cell, string(who) + " expects a vector target");
}

static CellPtr builtin_int_binary(
    const vector<CellPtr>& arguments,
    CellPtr current_vm,
    const char* who,
    IntBinaryOperation operation) {
    BuiltinVmValidation validation = expect_builtin_vm(arguments, move(current_vm), who, 3);
    if (validation.error) {
        return validation.error;
    }
    shared_ptr<VmCell> root_cell = validation.vm;

    const CellPtr left_cell = evaluate_int_or_error(arguments[1], root_cell, who);
    if (is_signal_cell(left_cell)) {
        return left_cell;
    }

    const CellPtr right_cell = evaluate_int_or_error(arguments[2], root_cell, who);
    if (is_signal_cell(right_cell)) {
        return right_cell;
    }

    const int64_t left = static_cast<const IntCell&>(*left_cell).value;
    const int64_t right = static_cast<const IntCell&>(*right_cell).value;
    return make_shared<IntCell>(operation(left, right));
}

static int64_t add_ints(int64_t left, int64_t right) {
    return left + right;
}

static int64_t sub_ints(int64_t left, int64_t right) {
    return left - right;
}

static int64_t mul_ints(int64_t left, int64_t right) {
    return left * right;
}

static CellPtr builtin_show(const vector<CellPtr>& arguments, CellPtr current_vm) {
    BuiltinVmValidation validation = expect_builtin_vm(arguments, move(current_vm), "show");
    if (validation.error) {
        return validation.error;
    }
    shared_ptr<VmCell> root_cell = validation.vm;

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
    return builtin_int_binary(
        arguments,
        move(current_vm),
        "add",
        add_ints);
}

static CellPtr builtin_sub(const vector<CellPtr>& arguments, CellPtr current_vm) {
    return builtin_int_binary(
        arguments,
        move(current_vm),
        "sub",
        sub_ints);
}

static CellPtr builtin_mul(const vector<CellPtr>& arguments, CellPtr current_vm) {
    return builtin_int_binary(
        arguments,
        move(current_vm),
        "mul",
        mul_ints);
}

static CellPtr builtin_div(const vector<CellPtr>& arguments, CellPtr current_vm) {
    BuiltinVmValidation validation = expect_builtin_vm(arguments, move(current_vm), "div", 3);
    if (validation.error) {
        return validation.error;
    }
    shared_ptr<VmCell> root_cell = validation.vm;

    const CellPtr left_cell = evaluate_int_or_error(arguments[1], root_cell, "div");
    if (is_signal_cell(left_cell)) {
        return left_cell;
    }

    const CellPtr right_cell = evaluate_int_or_error(arguments[2], root_cell, "div");
    if (is_signal_cell(right_cell)) {
        return right_cell;
    }

    const int64_t left = static_cast<const IntCell&>(*left_cell).value;
    const int64_t right = static_cast<const IntCell&>(*right_cell).value;
    if (right == 0) {
        return make_error_at("div cannot divide by zero", arguments[2]);
    }

    return make_shared<IntCell>(left / right);
}

static CellPtr builtin_mod(const vector<CellPtr>& arguments, CellPtr current_vm) {
    BuiltinVmValidation validation = expect_builtin_vm(arguments, move(current_vm), "mod", 3);
    if (validation.error) {
        return validation.error;
    }
    shared_ptr<VmCell> root_cell = validation.vm;

    const CellPtr left_cell = evaluate_int_or_error(arguments[1], root_cell, "mod");
    if (is_signal_cell(left_cell)) {
        return left_cell;
    }

    const CellPtr right_cell = evaluate_int_or_error(arguments[2], root_cell, "mod");
    if (is_signal_cell(right_cell)) {
        return right_cell;
    }

    const int64_t left = static_cast<const IntCell&>(*left_cell).value;
    const int64_t right = static_cast<const IntCell&>(*right_cell).value;
    if (right == 0) {
        return make_error_at("mod cannot divide by zero", arguments[2]);
    }

    return make_shared<IntCell>(left % right);
}

static CellPtr builtin_set(const vector<CellPtr>& arguments, CellPtr current_vm) {
    BuiltinVmValidation validation = expect_builtin_vm(arguments, move(current_vm), "set", 3);
    if (validation.error) {
        return validation.error;
    }
    shared_ptr<VmCell> root_cell = validation.vm;

    const CellPtr name_cell = arguments[1];
    if (!name_cell || name_cell->type != Cell::Type::string) {
        return make_error_at("set expects a string name", name_cell);
    }

    const CellPtr value = evaluate_or_signal(arguments[2], root_cell);
    if (is_signal_cell(value)) {
        return value;
    }

    const string& name = static_cast<const StrCell&>(*name_cell).value;
    shared_ptr<MapCell> target_scope = nearest_scope_like(name_cell, root_cell);
    if (name.find('.') == string::npos) {
        CellPtr assignment_error = assign_field_or_slot(target_scope, name, value);
        if (assignment_error) {
            return assignment_error;
        }
        return value;
    }

    CellPtr target_cell = resolve_name_like(name_cell, root_cell);
    if (!is_signal_cell(target_cell)) {
        shared_ptr<MapCell> target_parent = expect_map_cell(target_cell ? target_cell->parent : nullptr);
        if (is_typed_slot(target_parent)) {
            target_parent = expect_map_cell(target_parent->parent);
        }
        if (!target_parent) {
            return make_error_at("set dotted target must resolve to a map-like parent", name_cell);
        }

        size_t last_dot = name.rfind('.');
        CellPtr assignment_error = assign_field_or_slot(target_parent, name.substr(last_dot + 1), value);
        if (assignment_error) {
            return assignment_error;
        }
        return value;
    }

    shared_ptr<MapCell> error_details = materialize_error_details(static_cast<ErrCell&>(*target_cell));
    const StrCell* type_cell = error_details ? map_field_string(error_details, CellField::type) : nullptr;
    const StrCell* prefix_cell = error_details ? map_field_string(error_details, CellField::resolved_prefix) : nullptr;
    const StrCell* segment_cell = error_details ? map_field_string(error_details, CellField::failed_segment) : nullptr;
    if (!type_cell || type_cell->value != CellValue::lookup_error || !prefix_cell || !segment_cell) {
        return target_cell;
    }

    shared_ptr<StrCell> prefix_name = make_shared<StrCell>(prefix_cell->value);
    prefix_name->parent = name_cell->parent;
    CellPtr receiver_cell = resolve_name_like(prefix_name, root_cell);
    if (is_signal_cell(receiver_cell)) {
        return receiver_cell;
    }

    shared_ptr<MapCell> receiver_map = expect_map_cell(receiver_cell);
    if (!receiver_map) {
        return make_error_at("set dotted target must resolve to a map-like cell", name_cell);
    }

    CellPtr assignment_error = assign_field_or_slot(receiver_map, segment_cell->value, value);
    if (assignment_error) {
        return assignment_error;
    }
    return value;
}

static bool is_user_function(ConstCellPtr cell) {
    const StrCell* type = map_field_string(cell, CellField::type);
    return type && type->value == CellValue::function
        && map_field_vec(cell, CellField::params)
        && map_field_vec(cell, CellField::body);
}

static CellPtr expect_user_function(CellPtr cell, const char* who) {
    if (is_signal_cell(cell)) {
        return cell;
    }
    if (!is_user_function(cell)) {
        return make_error_at(string(who) + " expects a function object", cell);
    }
    return cell;
}

static CellPtr evaluate_call_arguments(
    ConstCellPtr arguments_cell,
    const shared_ptr<VmCell>& root_cell,
    vector<CellPtr>& evaluated_arguments) {
    if (!arguments_cell || arguments_cell->type != Cell::Type::vec) {
        return make_error_at("call expects an argument vector", arguments_cell);
    }

    const VecCell& argument_expressions = static_cast<const VecCell&>(*arguments_cell);
    evaluated_arguments.reserve(argument_expressions.value.size());
    for (const CellPtr& argument_expression : argument_expressions.value) {
        CellPtr argument = evaluate_or_signal(argument_expression, root_cell);
        if (is_signal_cell(argument)) {
            return argument;
        }
        evaluated_arguments.push_back(argument);
    }
    return nullptr;
}

static CellPtr bind_call_arguments(
    const shared_ptr<ScopeCell>& call_scope,
    ConstCellPtr params_cell,
    const vector<CellPtr>& evaluated_arguments,
    const char* who) {
    const VecCell& params = static_cast<const VecCell&>(*params_cell);
    if (params.value.size() != evaluated_arguments.size()) {
        return make_error(string(who) + " argument count does not match function parameters");
    }

    for (ConstCellPtr param_cell : params.value) {
        if (!param_cell || param_cell->type != Cell::Type::string) {
            return make_error_at("function parameters must be strings", param_cell);
        }
    }

    for (size_t index = 0; index < params.value.size(); ++index) {
        ConstCellPtr param_cell = params.value[index];
        call_scope->set(static_cast<const StrCell&>(*param_cell).value, clone_cell_tree(evaluated_arguments[index]));
    }
    return nullptr;
}

static CellPtr evaluate_function_body(const shared_ptr<ScopeCell>& call_scope, const shared_ptr<VmCell>& root_cell) {
    shared_ptr<VecCell> body = map_field_vec(call_scope, CellField::internal_body);
    if (!body) {
        return make_error("call scope is missing a function body");
    }

    CellPtr result = make_shared<NilCell>();
    for (const CellPtr& expression : body->value) {
        result = evaluate_or_signal(expression, root_cell);
        if (result && result->type == Cell::Type::return_signal) {
            return clone_cell_tree(static_cast<const RetCell&>(*result).value);
        }
        if (is_signal_cell(result)) {
            return result;
        }
    }

    return clone_cell_tree(result);
}

static CellPtr invoke_resolved_user_function(
    CellPtr function_cell,
    const vector<CellPtr>& evaluated_arguments,
    const shared_ptr<VmCell>& root_cell,
    const char* who) {
    shared_ptr<ScopeCell> call_scope = make_shared<ScopeCell>();
    call_scope->parent = function_cell;

    CellPtr binding_error = bind_call_arguments(
        call_scope,
        map_field_vec(function_cell, CellField::params),
        evaluated_arguments,
        who);
    if (binding_error) {
        call_scope->parent = nullptr;
        return binding_error;
    }

    call_scope->set(CellField::internal_body, clone_cell_tree(map_field_vec(function_cell, CellField::body)));
    CellPtr result = evaluate_function_body(call_scope, root_cell);
    call_scope->clear_descendant_parent_links();
    call_scope->parent = nullptr;
    return result;
}

static CellPtr builtin_call(const vector<CellPtr>& arguments, CellPtr current_vm) {
    BuiltinVmValidation validation = expect_builtin_vm(arguments, move(current_vm), "call", 3);
    if (validation.error) {
        return validation.error;
    }
    shared_ptr<VmCell> root_cell = validation.vm;

    CellPtr function_cell = expect_user_function(resolve_or_signal(arguments[1], root_cell), "call");
    if (is_signal_cell(function_cell)) {
        return function_cell;
    }

    vector<CellPtr> evaluated_arguments;
    CellPtr argument_error = evaluate_call_arguments(arguments[2], root_cell, evaluated_arguments);
    if (argument_error) {
        return argument_error;
    }

    return invoke_resolved_user_function(function_cell, evaluated_arguments, root_cell, "call");
}

CellPtr invoke_message_handler(CellPtr handler_expression, CellPtr message, CellPtr current_vm) {
    shared_ptr<VmCell> root_cell = expect_vm_cell(move(current_vm));
    if (!root_cell) {
        return make_error("message handler requires a map VM");
    }

    CellPtr function_cell = expect_user_function(
        resolve_or_signal(move(handler_expression), root_cell),
        "message handler");
    if (is_signal_cell(function_cell)) {
        return function_cell;
    }

    vector<CellPtr> message_argument {
        message ? move(message) : static_pointer_cast<Cell>(make_shared<NilCell>()),
    };
    return invoke_resolved_user_function(function_cell, message_argument, root_cell, "message handler");
}

static CellPtr builtin_return(const vector<CellPtr>& arguments, CellPtr current_vm) {
    BuiltinVmValidation validation = expect_builtin_vm(arguments, move(current_vm), "return");
    if (validation.error) {
        return validation.error;
    }
    shared_ptr<VmCell> root_cell = validation.vm;

    CellPtr value = evaluate_or_signal(arguments[1], root_cell);
    if (is_signal_cell(value)) {
        return value;
    }

    return make_shared<RetCell>(value);
}

static CellPtr builtin_eval(const vector<CellPtr>& arguments, CellPtr current_vm) {
    BuiltinVmValidation validation = expect_builtin_vm(arguments, move(current_vm), "eval");
    if (validation.error) {
        return validation.error;
    }
    shared_ptr<VmCell> root_cell = validation.vm;

    CellPtr code = evaluate_or_signal(arguments[1], root_cell);
    if (is_signal_cell(code)) {
        return code;
    }

    return evaluate_or_signal(code, root_cell);
}

static CellPtr builtin_list(const vector<CellPtr>& arguments, CellPtr current_vm) {
    BuiltinVmValidation validation = expect_builtin_vm(arguments, move(current_vm), "list", 1);
    if (validation.error) {
        return validation.error;
    }

    shared_ptr<VecCell> names = make_shared<VecCell>();
    for (string& name : visible_scope_names(arguments.front(), validation.vm)) {
        names->append(make_shared<StrCell>(move(name)));
    }
    return names;
}

static CellPtr builtin_run(const vector<CellPtr>& arguments, CellPtr current_vm) {
    BuiltinVmValidation validation = expect_builtin_vm(arguments, move(current_vm), "run");
    if (validation.error) {
        return validation.error;
    }
    shared_ptr<VmCell> root_cell = validation.vm;

    CellPtr sequence_cell = resolve_vec_or_signal(arguments[1], root_cell, "run expects a vector sequence");
    if (is_signal_cell(sequence_cell)) {
        return sequence_cell;
    }

    if (!arm_sequence_frame(root_cell, sequence_cell)) {
        return make_error("run sequence could not be represented as an object path");
    }
    return make_shared<NilCell>();
}

static CellPtr builtin_append(const vector<CellPtr>& arguments, CellPtr current_vm) {
    BuiltinVmValidation validation = expect_builtin_vm(arguments, move(current_vm), "append", 3);
    if (validation.error) {
        return validation.error;
    }
    shared_ptr<VmCell> root_cell = validation.vm;

    CellPtr sequence_cell = resolve_vec_target_or_signal(arguments[1], root_cell, "append");
    if (is_signal_cell(sequence_cell)) {
        return sequence_cell;
    }
    shared_ptr<VecCell> sequence = static_pointer_cast<VecCell>(sequence_cell);

    CellPtr value = evaluate_or_signal(arguments[2], root_cell);
    if (is_signal_cell(value)) {
        return value;
    }

    sequence->append(value);
    return sequence;
}

static CellPtr builtin_pop(const vector<CellPtr>& arguments, CellPtr current_vm) {
    BuiltinVmValidation validation = expect_builtin_vm(arguments, move(current_vm), "pop");
    if (validation.error) {
        return validation.error;
    }
    shared_ptr<VmCell> root_cell = validation.vm;

    CellPtr sequence_cell = resolve_vec_target_or_signal(arguments[1], root_cell, "pop");
    if (is_signal_cell(sequence_cell)) {
        return sequence_cell;
    }
    shared_ptr<VecCell> sequence = static_pointer_cast<VecCell>(sequence_cell);

    if (sequence->value.empty()) {
        return make_error_at("pop expects a non-empty vector", arguments[1]);
    }

    CellPtr value = sequence->value.back();
    sequence->value.pop_back();
    return value;
}

static CellPtr builtin_at(const vector<CellPtr>& arguments, CellPtr current_vm) {
    BuiltinVmValidation validation = expect_builtin_vm(arguments, move(current_vm), "at", 3);
    if (validation.error) {
        return validation.error;
    }
    shared_ptr<VmCell> root_cell = validation.vm;

    CellPtr sequence_cell = resolve_vec_target_or_signal(arguments[1], root_cell, "at");
    if (is_signal_cell(sequence_cell)) {
        return sequence_cell;
    }
    shared_ptr<VecCell> sequence = static_pointer_cast<VecCell>(sequence_cell);

    const CellPtr index_cell = evaluate_int_or_error(arguments[2], root_cell, "at");
    if (is_signal_cell(index_cell)) {
        return index_cell;
    }

    const int64_t index = static_cast<const IntCell&>(*index_cell).value;
    if (index < 0 || static_cast<size_t>(index) >= sequence->value.size()) {
        return make_error_at("at index is out of bounds", arguments[2]);
    }

    return sequence->value[static_cast<size_t>(index)];
}

static CellPtr builtin_get(const vector<CellPtr>& arguments, CellPtr current_vm) {
    BuiltinVmValidation validation = expect_builtin_vm(arguments, move(current_vm), "get");
    if (validation.error) {
        return validation.error;
    }
    shared_ptr<VmCell> root_cell = validation.vm;

    CellPtr path_cell = resolve_vec_or_signal(arguments[1], root_cell, "get expects a vector path");
    if (is_signal_cell(path_cell)) {
        return path_cell;
    }

    return cell_at_path(root_cell, path_cell);
}

static CellPtr builtin_copy(const vector<CellPtr>& arguments, CellPtr current_vm) {
    BuiltinVmValidation validation = expect_builtin_vm(arguments, move(current_vm), "copy");
    if (validation.error) {
        return validation.error;
    }
    shared_ptr<VmCell> root_cell = validation.vm;

    CellPtr value = resolve_or_signal(arguments[1], root_cell);
    if (is_signal_cell(value)) {
        return value;
    }

    if (!value) {
        return nullptr;
    }

    return clone_cell_tree(value);
}

static CellPtr builtin_if(const vector<CellPtr>& arguments, CellPtr current_vm) {
    BuiltinVmValidation validation = expect_builtin_vm(arguments, move(current_vm), "if", 4);
    if (validation.error) {
        return validation.error;
    }
    shared_ptr<VmCell> root_cell = validation.vm;

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
    BuiltinVmValidation validation = expect_builtin_vm(arguments, move(current_vm), "while", 3);
    if (validation.error) {
        return validation.error;
    }
    shared_ptr<VmCell> root_cell = validation.vm;

    CellPtr body_cell = resolve_vec_or_signal(arguments[2], root_cell, "while expects a vector body");
    if (is_signal_cell(body_cell)) {
        return body_cell;
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

    BuiltinVmValidation validation = resolve_child_vm(arguments, move(current_vm), "start");
    if (validation.error) {
        return validation.error;
    }
    shared_ptr<VmCell> child_vm = validation.vm;

    while (true) {
        CellPtr step_result = advance_vm_fn(child_vm);
        if (is_signal_cell(step_result)) {
            return step_result;
        }

        if (vm_is_terminal(child_vm)) {
            return step_result;
        }

        const StrCell* yield_reason = map_field_string(ensure_vm_state(child_vm), CellField::yield_reason);
        if (yield_reason && yield_reason->value == CellValue::breakpoint) {
            return step_result;
        }
    }
}

static CellPtr builtin_step(const vector<CellPtr>& arguments, CellPtr current_vm) {
    if (!advance_vm_fn) {
        return make_error("step is not initialized");
    }

    BuiltinVmValidation validation = resolve_child_vm(arguments, move(current_vm), "step");
    if (validation.error) {
        return validation.error;
    }
    shared_ptr<VmCell> child_vm = validation.vm;

    advance_vm_fn(child_vm);
    CellPtr status_cell = vm_status_cell(child_vm);
    if (!status_cell) {
        return make_error("step requires child VM state");
    }

    return status_cell;
}

static void install_builtin(const shared_ptr<ScopeCell>& zygote, const string& name, FunCell::Implementation implementation) {
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

shared_ptr<ScopeCell> make_zygote() {
    shared_ptr<ScopeCell> zygote = make_shared<ScopeCell>();
    install_builtin(zygote, "show", builtin_show);
    install_builtin(zygote, "add", builtin_add);
    install_builtin(zygote, "sub", builtin_sub);
    install_builtin(zygote, "mul", builtin_mul);
    install_builtin(zygote, "div", builtin_div);
    install_builtin(zygote, "mod", builtin_mod);
    install_builtin(zygote, "set", builtin_set);
    install_builtin(zygote, "eval", builtin_eval);
    install_builtin(zygote, "list", builtin_list);
    install_builtin(zygote, "run", builtin_run);
    install_builtin(zygote, "append", builtin_append);
    install_builtin(zygote, "pop", builtin_pop);
    install_builtin(zygote, "at", builtin_at);
    install_builtin(zygote, "get", builtin_get);
    install_builtin(zygote, "copy", builtin_copy);
    install_builtin(zygote, "call", builtin_call);
    install_builtin(zygote, "return", builtin_return);
    install_builtin(zygote, "if", builtin_if);
    install_builtin(zygote, "while", builtin_while);
    install_builtin(zygote, "start", builtin_start);
    install_builtin(zygote, "step", builtin_step);
    return zygote;
}
