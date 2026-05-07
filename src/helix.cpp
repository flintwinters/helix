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

static shared_ptr<MapCell> ensure_vm_state(const shared_ptr<MapCell>& vm) {
    unordered_map<string, CellPtr>::const_iterator state_it = vm->value.find("state");
    shared_ptr<MapCell> state = nullptr;
    if (state_it != vm->value.end() && state_it->second && state_it->second->type == Cell::Type::map) {
        state = static_pointer_cast<MapCell>(state_it->second);
    } else {
        state = make_shared<MapCell>();
        vm->set("state", state);
    }

    unordered_map<string, CellPtr>::const_iterator status_it = state->value.find("status");
    if (status_it == state->value.end() || !status_it->second || status_it->second->type != Cell::Type::string) {
        state->set("status", make_shared<StrCell>("ready"));
    }

    unordered_map<string, CellPtr>::const_iterator frames_it = state->value.find("frames");
    if (frames_it == state->value.end() || !frames_it->second || frames_it->second->type != Cell::Type::vec) {
        state->set("frames", make_shared<VecCell>());
    }

    return state;
}

static shared_ptr<VecCell> vm_frames(const shared_ptr<MapCell>& vm) {
    shared_ptr<MapCell> state = ensure_vm_state(vm);
    return static_pointer_cast<VecCell>(state->value["frames"]);
}

static string vm_status(const shared_ptr<MapCell>& vm) {
    shared_ptr<MapCell> state = ensure_vm_state(vm);
    return static_cast<const StrCell&>(*state->value["status"]).value;
}

static void set_vm_status(const shared_ptr<MapCell>& vm, const string& status) {
    shared_ptr<MapCell> state = ensure_vm_state(vm);
    state->set("status", make_shared<StrCell>(status));
}

static void clear_vm_terminal_fields(const shared_ptr<MapCell>& vm) {
    shared_ptr<MapCell> state = ensure_vm_state(vm);
    state->value.erase("result");
    state->value.erase("error");
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
    if (is_signal_cell(actor)) {
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

    if (is_signal_cell(node)) {
        return node;
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

static CellPtr advance_list_frame(const shared_ptr<MapCell>& vm, const shared_ptr<MapCell>& frame) {
    unordered_map<string, CellPtr>::const_iterator values_it = frame->value.find("values");
    if (values_it == frame->value.end() || !values_it->second || values_it->second->type != Cell::Type::vec) {
        CellPtr error = make_error_cell("list frame is missing a vector sequence");
        attach_terminal_state(vm, error);
        return error;
    }

    unordered_map<string, CellPtr>::const_iterator index_it = frame->value.find("index");
    if (index_it == frame->value.end() || !index_it->second || index_it->second->type != Cell::Type::integer) {
        CellPtr error = make_error_cell("list frame is missing an integer index");
        attach_terminal_state(vm, error);
        return error;
    }

    shared_ptr<VecCell> sequence = static_pointer_cast<VecCell>(values_it->second);
    int64_t index = static_cast<const IntCell&>(*index_it->second).value;
    if (index < 0 || static_cast<size_t>(index) >= sequence->value.size()) {
        CellPtr error = make_error_cell("list frame index is out of bounds");
        attach_terminal_state(vm, error);
        return error;
    }

    CellPtr value = evaluate_cell(sequence->value[static_cast<size_t>(index)], vm);
    if (is_signal_cell(value)) {
        attach_terminal_state(vm, value);
        return value;
    }

    size_t next_index = static_cast<size_t>(index) + 1;
    if (next_index >= sequence->value.size()) {
        shared_ptr<MapCell> state = ensure_vm_state(vm);
        state->set("frames", make_shared<VecCell>());
        CellPtr result = make_shared<NilCell>();
        attach_terminal_state(vm, result);
        return result;
    }

    frame->set("index", make_shared<IntCell>(static_cast<int64_t>(next_index)));
    shared_ptr<MapCell> state = ensure_vm_state(vm);
    state->set("frames", make_shared<VecCell>(vector<CellPtr> {frame}));
    state->set("status", make_shared<StrCell>("running"));
    clear_vm_terminal_fields(vm);
    return value;
}

static CellPtr advance_vm(const shared_ptr<MapCell>& vm) {
    ensure_vm_state(vm);
    string status = vm_status(vm);
    if (status == "finished" || status == "error" || status == "signaled") {
        shared_ptr<MapCell> state = ensure_vm_state(vm);
        unordered_map<string, CellPtr>::const_iterator result_it = state->value.find("result");
        if (result_it != state->value.end()) {
            return result_it->second;
        }
        return nullptr;
    }

    shared_ptr<VecCell> frames = vm_frames(vm);
    if (frames->value.empty()) {
        unordered_map<string, CellPtr>::const_iterator main_it = vm->value.find("main");
        if (main_it == vm->value.end()) {
            CellPtr error = make_error_cell("program is missing a main entrypoint");
            attach_terminal_state(vm, error);
            return error;
        }

        set_vm_status(vm, "running");
        clear_vm_terminal_fields(vm);

        CellPtr result = is_null_cell(main_it->second) ? main_it->second : evaluate_cell(main_it->second, vm);
        if (is_signal_cell(result)) {
            attach_terminal_state(vm, result);
            return result;
        }

        frames = vm_frames(vm);
        if (frames->value.empty()) {
            attach_terminal_state(vm, result);
            return result;
        }
    }

    shared_ptr<VecCell> resumed_frames = vm_frames(vm);
    if (resumed_frames->value.empty()) {
        return nullptr;
    }

    const CellPtr frame_cell = resumed_frames->value.front();
    if (!frame_cell || frame_cell->type != Cell::Type::map) {
        CellPtr error = make_error_cell("VM frame is not a map");
        attach_terminal_state(vm, error);
        return error;
    }

    shared_ptr<MapCell> frame = static_pointer_cast<MapCell>(frame_cell);
    unordered_map<string, CellPtr>::const_iterator name_it = frame->value.find("name");
    if (name_it == frame->value.end() || !name_it->second || name_it->second->type != Cell::Type::string) {
        CellPtr error = make_error_cell("VM frame is missing a string name");
        attach_terminal_state(vm, error);
        return error;
    }

    const string& frame_name = static_cast<const StrCell&>(*name_it->second).value;
    if (frame_name == "list") {
        return advance_list_frame(vm, frame);
    }

    CellPtr error = make_error_cell("unknown VM frame type");
    attach_terminal_state(vm, error);
    return error;
}

static CellPtr run_vm(const shared_ptr<MapCell>& vm) {
    while (true) {
        string status = vm_status(vm);
        if (status == "finished" || status == "error" || status == "signaled") {
            shared_ptr<MapCell> state = ensure_vm_state(vm);
            unordered_map<string, CellPtr>::const_iterator result_it = state->value.find("result");
            if (result_it != state->value.end()) {
                return result_it->second;
            }
            return nullptr;
        }

        advance_vm(vm);
    }
}

static void run_main(shared_ptr<MapCell> root_cell) {
    run_vm(root_cell);
}

int main(int argc, char* argv[]) {
    if (argc != 2) {
        std::cerr << "usage: ./helix <program.yaml>\n";
        return 1;
    }

    try {
        initialize_builtins(evaluate_cell, resolve_cell, advance_vm, run_vm, render_show_output, make_error_cell);
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
