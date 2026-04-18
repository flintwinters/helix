#include "utils.hpp"

#include <utility>

using namespace std;

static Ptr ensure_state_map(const Ptr& vm) {
    shared_ptr<Map> vm_map = dynamic_pointer_cast<Map>(vm);
    if (!vm_map) {
        return error("vm is not a map");
    }

    unordered_map<string, Ptr>::iterator existing = vm_map->m.find("state");
    if (existing != vm_map->m.end()) {
        shared_ptr<Map> state = dynamic_pointer_cast<Map>(existing->second);
        if (state) {
            return existing->second;
        }
        return error("state is not a map");
    }

    Ptr state = make_cell(new Map({
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
    Ptr state_cell = vm->find(vm, "state");
    if (dynamic_pointer_cast<Err>(state_cell)) {
        return state_cell;
    }

    shared_ptr<Map> state = dynamic_pointer_cast<Map>(state_cell);
    if (!state) {
        return error("state is not a map");
    }

    Ptr args = state->find(state_cell, "args");
    if (dynamic_pointer_cast<Err>(args)) {
        return args;
    }

    shared_ptr<Vec> values = dynamic_pointer_cast<Vec>(args);
    if (!values) {
        return error("args is not a vector");
    }

    return args;
}

Ptr eval_with_args(const Ptr& vm, const Ptr& actor, vector<Ptr> args) {
    Ptr state_cell = ensure_state_map(vm);
    if (dynamic_pointer_cast<Err>(state_cell)) {
        return state_cell;
    }
    shared_ptr<Map> state = dynamic_pointer_cast<Map>(state_cell);

    unordered_map<string, Ptr>::iterator previous = state->m.find("args");
    const bool had_previous = previous != state->m.end();
    Ptr previous_args = had_previous ? previous->second : Ptr{};

    state->m["args"] = make_cell(new Vec(move(args)));
    Ptr result = actor->eval(vm);

    if (had_previous) {
        state->m["args"] = previous_args;
    } else {
        state->m.erase("args");
    }

    return result;
}
