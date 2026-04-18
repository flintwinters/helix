#include "core_utils.hpp"

#include <utility>

using namespace std;

static Ptr ensure_state_map(const Ptr& vm) {
    auto vm_map = dynamic_pointer_cast<Map>(vm);
    if (!vm_map) {
        return error("vm is not a map");
    }

    auto existing = vm_map->m.find("state");
    if (existing != vm_map->m.end()) {
        auto state = dynamic_pointer_cast<Map>(existing->second);
        if (state) {
            return existing->second;
        }
        return error("state is not a map");
    }

    auto state = make_cell(new Map({
        {"args", make_cell(new Vec({}))}
    }));
    vm_map->m["state"] = state;
    return state;
}

string pad(size_t depth) {
    return string(depth * 2, ' ');
}

Ptr error(string s) {
    return make_cell(new Err(move(s)));
}

Ptr current_args(const Ptr& vm) {
    auto state_cell = vm->find(vm, "state");
    if (dynamic_pointer_cast<Err>(state_cell)) {
        return state_cell;
    }

    auto state = dynamic_pointer_cast<Map>(state_cell);
    if (!state) {
        return error("state is not a map");
    }

    auto args = state->find(state_cell, "args");
    if (dynamic_pointer_cast<Err>(args)) {
        return args;
    }

    auto values = dynamic_pointer_cast<Vec>(args);
    if (!values) {
        return error("args is not a vector");
    }

    return args;
}

Ptr eval_with_args(const Ptr& vm, const Ptr& actor, vector<Ptr> args) {
    auto state_cell = ensure_state_map(vm);
    if (dynamic_pointer_cast<Err>(state_cell)) {
        return state_cell;
    }
    auto state = dynamic_pointer_cast<Map>(state_cell);

    auto previous = state->m.find("args");
    const bool had_previous = previous != state->m.end();
    Ptr previous_args = had_previous ? previous->second : Ptr{};

    state->m["args"] = make_cell(new Vec(move(args)));
    auto result = actor->eval(vm);

    if (had_previous) {
        state->m["args"] = previous_args;
    } else {
        state->m.erase("args");
    }

    return result;
}
