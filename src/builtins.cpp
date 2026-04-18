#include <unordered_map>
#include <utility>
#include <vector>

#include "core.hpp"
#include "utils.hpp"

using namespace std;

static Ptr parse_binary_int_args(const Ptr& vm, const char* builtin_name, shared_ptr<Int>& left, shared_ptr<Int>& right) {
    Ptr args_cell = current_args(vm);
    if (dynamic_pointer_cast<Err>(args_cell)) {
        return args_cell;
    }

    shared_ptr<Vec> args = dynamic_pointer_cast<Vec>(args_cell);
    if (!args || args->v.size() < 2) {
        return error(string(builtin_name) + " expects two arguments");
    }

    left = dynamic_pointer_cast<Int>(args->v[0]);
    right = dynamic_pointer_cast<Int>(args->v[1]);
    if (!left || !right) {
        return error(string(builtin_name) + " expects integer arguments");
    }

    return nullptr;
}

static Ptr add_builtin(const Ptr& vm) {
    shared_ptr<Int> left;
    shared_ptr<Int> right;
    Ptr error_cell = parse_binary_int_args(vm, "add", left, right);
    if (error_cell) {
        return error_cell;
    }

    return make_cell(new Int(left->v + right->v));
}

static Ptr sub_builtin(const Ptr& vm) {
    shared_ptr<Int> left;
    shared_ptr<Int> right;
    Ptr error_cell = parse_binary_int_args(vm, "sub", left, right);
    if (error_cell) {
        return error_cell;
    }

    return make_cell(new Int(left->v - right->v));
}

Ptr make_builtin_env() {
    return make_cell(new Map({
        {"add", make_cell(new Fun(add_builtin))},
        {"sub", make_cell(new Fun(sub_builtin))}
    }));
}
