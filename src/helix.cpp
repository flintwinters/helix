#include <iostream>
#include <memory>
#include <optional>
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

static CellPtr fail_vm(const shared_ptr<VmCell>& vm, const string& message) {
    CellPtr error = make_error_cell(message);
    attach_terminal_state(vm, error);
    return error;
}

static CellPtr evaluate_cell(CellPtr node, const shared_ptr<VmCell>& root_cell);

static void clear_vm_yield_reason(const shared_ptr<VmCell>& vm) {
    ensure_vm_state(vm)->value.erase("yield_reason");
}

static CellPtr breakpoint_error(const string& message, ConstCellPtr source) {
    shared_ptr<MapCell> details = make_shared<MapCell>();
    details->set("kind", make_shared<StrCell>("breakpoint_error"));
    if (source) {
        details->set("source", const_pointer_cast<Cell>(source));
    }
    return make_error_cell(message, details);
}

static bool vm_breakpoint_matches(const shared_ptr<VmCell>& vm, ConstCellPtr pc, CellPtr& error) {
    CellPtr breakpoints_cell = map_field_cell(vm, "breakpoints");
    if (!breakpoints_cell) {
        return false;
    }

    if (breakpoints_cell->type != Cell::Type::vec) {
        error = breakpoint_error("VM breakpoints must be a vector", breakpoints_cell);
        return false;
    }

    const vector<CellPtr>& breakpoints = static_cast<const VecCell&>(*breakpoints_cell).value;
    for (const CellPtr& breakpoint : breakpoints) {
        if (!breakpoint || breakpoint->type != Cell::Type::vec) {
            error = breakpoint_error("VM breakpoint entries must be object path vectors", breakpoint);
            return false;
        }

        if (cell_paths_equal(pc, breakpoint)) {
            return true;
        }
    }

    return false;
}

static CellPtr yield_at_breakpoint(const shared_ptr<VmCell>& vm, CellPtr pc) {
    shared_ptr<MapCell> state = ensure_vm_state(vm);
    state->set("pc", move(pc));
    state->set("yield_reason", make_shared<StrCell>("breakpoint"));
    set_vm_status(vm, VmStatus::running);
    clear_vm_terminal_fields(vm);
    return vm_status_cell(vm);
}

static CellPtr update_pc_or_break(const shared_ptr<VmCell>& vm, CellPtr node) {
    CellPtr pc = cell_path_from_root(vm, node);
    if (!pc) {
        return fail_vm(vm, "VM program counter could not be resolved as an object path");
    }

    ensure_vm_state(vm)->set("pc", pc);

    CellPtr error = nullptr;
    if (vm_breakpoint_matches(vm, pc, error)) {
        return yield_at_breakpoint(vm, pc);
    }
    if (error) {
        attach_terminal_state(vm, error);
        return error;
    }

    clear_vm_yield_reason(vm);
    return nullptr;
}

static shared_ptr<MapCell> make_resolution_details(
    const string& kind,
    const string& name,
    const Cell* context,
    CellPtr source = nullptr) {
    unordered_map<string, CellPtr> fields;
    fields["kind"] = make_shared<StrCell>(kind);
    fields["name"] = make_shared<StrCell>(name);
    fields["context_type"] = make_shared<StrCell>(cell_class_name(context));
    if (source) {
        fields["source"] = source;
    }
    return make_shared<MapCell>(move(fields));
}

static void attach_error_source(ErrCell& error, CellPtr source) {
    if (!source || !is_map_like_cell(error.value)) {
        return;
    }

    static_pointer_cast<MapCell>(error.value)->set("source", move(source));
}

static const Cell* lookup_context(ConstCellPtr node, const shared_ptr<VmCell>& root_cell) {
    for (ConstCellPtr current = node; current; current = current->parent) {
        if (current->type == Cell::Type::scope || current->type == Cell::Type::vm) {
            return current.get();
        }
    }

    return root_cell.get();
}

static CellPtr resolve_cell(CellPtr node, const shared_ptr<VmCell>& root_cell) {
    if (!node) {
        return nullptr;
    }

    if (node->type != Cell::Type::string) {
        return node;
    }

    const auto& name = static_cast<const StrCell&>(*node).value;
    const Cell* context = lookup_context(node, root_cell);
    CellPtr resolved = context ? context->lookup(name, root_cell) : nullptr;
    if (resolved && resolved->type == Cell::Type::error_signal) {
        ErrCell& error = static_cast<ErrCell&>(*resolved);
        if (!error.value) {
            error.value = make_resolution_details("resolution_error", name, context, node);
        } else {
            attach_error_source(error, node);
        }
        return resolved;
    }
    if (resolved) {
        return resolved;
    }

    return node;
}

static CellPtr evaluate_form(const VecCell& form, const shared_ptr<VmCell>& root_cell) {
    if (form.value.empty()) {
        return make_error_cell("cannot evaluate an empty vector");
    }

    CellPtr actor = evaluate_cell(form.value.front(), root_cell);
    if (is_signal_cell(actor)) {
        return actor;
    }

    if (actor && actor.get() == form.value.front().get() && actor->type == Cell::Type::string) {
        const string& actor_name = static_cast<const StrCell&>(*actor).value;
        return make_error_cell(
            "vector actor could not be resolved",
            make_resolution_details("unresolved_actor", actor_name, root_cell.get(), form.value.front()));
    }

    if (!actor || actor->type != Cell::Type::function) {
        unordered_map<string, CellPtr> fields;
        fields["kind"] = make_shared<StrCell>("invalid_actor");
        fields["actor_type"] = make_shared<StrCell>(cell_class_name(actor));
        fields["source"] = form.value.front();
        shared_ptr<MapCell> details = make_shared<MapCell>(move(fields));
        return make_error_cell("vector actor did not resolve to a builtin", details);
    }

    return actor->call(form.value, root_cell);
}

static CellPtr evaluate_cell(CellPtr node, const shared_ptr<VmCell>& root_cell) {
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

static shared_ptr<VecCell> frame_values(const shared_ptr<ScopeCell>& frame) {
    return map_field_vec(frame, "values");
}

static const IntCell* frame_index(const shared_ptr<ScopeCell>& frame) {
    return map_field_int(frame, "index");
}

static CellPtr clone_path_segment(ConstCellPtr segment) {
    if (!segment) {
        return nullptr;
    }
    if (segment->type == Cell::Type::string) {
        return make_shared<StrCell>(static_cast<const StrCell&>(*segment).value);
    }
    if (segment->type == Cell::Type::integer) {
        return make_shared<IntCell>(static_cast<const IntCell&>(*segment).value);
    }
    return nullptr;
}

static shared_ptr<VecCell> clone_path_prefix(ConstCellPtr path_cell) {
    if (!path_cell || path_cell->type != Cell::Type::vec) {
        return nullptr;
    }

    const vector<CellPtr>& path = static_cast<const VecCell&>(*path_cell).value;
    if (path.empty()) {
        return nullptr;
    }

    vector<CellPtr> prefix;
    prefix.reserve(path.size() - 1);
    for (size_t index = 0; index + 1 < path.size(); ++index) {
        CellPtr segment = clone_path_segment(path[index]);
        if (!segment) {
            return nullptr;
        }
        prefix.push_back(move(segment));
    }

    return make_shared<VecCell>(move(prefix));
}

static shared_ptr<VecCell> resolve_sequence_path(
    const shared_ptr<VmCell>& vm,
    const shared_ptr<VecCell>& path) {
    CellPtr resolved = cell_at_path(vm, path);
    if (!resolved || resolved->type != Cell::Type::vec) {
        return nullptr;
    }

    return static_pointer_cast<VecCell>(resolved);
}

static shared_ptr<VecCell> sequence_path_from_pc(const shared_ptr<VmCell>& vm) {
    return clone_path_prefix(map_field_cell(ensure_vm_state(vm), "pc"));
}

static shared_ptr<VecCell> frame_sequence(const shared_ptr<VmCell>& vm, const shared_ptr<ScopeCell>& frame) {
    shared_ptr<VecCell> path = sequence_path_from_pc(vm);
    if (path) {
        shared_ptr<VecCell> sequence = resolve_sequence_path(vm, path);
        if (sequence) {
            return sequence;
        }
    }

    return frame_values(frame);
}

static void clear_vm_frames(const shared_ptr<VmCell>& vm) {
    ensure_vm_state(vm)->set("frames", make_shared<VecCell>());
}

static void store_list_resume_frame(const shared_ptr<VmCell>& vm, const shared_ptr<ScopeCell>& frame, size_t next_index) {
    frame->set("index", make_shared<IntCell>(static_cast<int64_t>(next_index)));
    shared_ptr<VecCell> frames = make_shared<VecCell>();
    frame->parent = frames;
    frames->value.push_back(frame);
    ensure_vm_state(vm)->set("frames", frames);
    set_vm_status(vm, VmStatus::running);
    clear_vm_terminal_fields(vm);
}

static void retire_unyielded_frame(const shared_ptr<ScopeCell>& frame) {
    frame->clear_descendant_parent_links();
    frame->value.clear();
    frame->parent = nullptr;
}

static optional<size_t> validated_list_frame_index(
    const shared_ptr<VmCell>& vm,
    const shared_ptr<VecCell>& sequence,
    const shared_ptr<ScopeCell>& frame) {
    const IntCell* index_cell = frame_index(frame);
    if (!index_cell) {
        fail_vm(vm, "list frame is missing an integer index");
        return nullopt;
    }

    int64_t index = index_cell->value;
    if (index < 0 || static_cast<size_t>(index) >= sequence->value.size()) {
        fail_vm(vm, "list frame index is out of bounds");
        return nullopt;
    }

    return static_cast<size_t>(index);
}

static CellPtr yield_list_frame_at(
    const shared_ptr<VmCell>& vm,
    const shared_ptr<ScopeCell>& frame,
    size_t next_index,
    CellPtr result) {
    store_list_resume_frame(vm, frame, next_index);
    return result;
}

struct ListFrameAdvance {
    shared_ptr<VmCell> vm {};
    shared_ptr<ScopeCell> frame {};
    shared_ptr<VecCell> sequence {};
    bool yielded = false;
};

static CellPtr advance_list_item(ListFrameAdvance& advance, size_t current_index) {
    CellPtr item = advance.sequence->value[current_index];
    CellPtr breakpoint_result = update_pc_or_break(advance.vm, item);
    if (breakpoint_result) {
        return is_signal_cell(breakpoint_result)
            ? breakpoint_result
            : yield_list_frame_at(advance.vm, advance.frame, current_index, breakpoint_result);
    }

    CellPtr value = evaluate_cell(item, advance.vm);
    if (is_signal_cell(value)) {
        attach_terminal_state(advance.vm, value);
        return value;
    }

    size_t next_index = current_index + 1;
    if (next_index < advance.sequence->value.size()) {
        advance.yielded = true;
        store_list_resume_frame(advance.vm, advance.frame, next_index);
    }

    return nullptr;
}

static CellPtr advance_list_frame(const shared_ptr<VmCell>& vm, const shared_ptr<ScopeCell>& frame) {
    shared_ptr<VecCell> sequence = frame_sequence(vm, frame);
    if (!sequence) {
        return fail_vm(vm, "list frame is missing a vector sequence");
    }

    optional<size_t> start_index = validated_list_frame_index(vm, sequence, frame);
    if (!start_index) {
        return vm_result(vm);
    }

    ListFrameAdvance advance {vm, frame, sequence};
    CellPtr result = advance_list_item(advance, *start_index);
    if (result) {
        return result;
    }

    if (!advance.yielded) {
        clear_vm_frames(vm);
        retire_unyielded_frame(frame);
    }
    return make_shared<NilCell>();
}

static CellPtr start_vm_main(const shared_ptr<VmCell>& vm) {
    unordered_map<string, CellPtr>::const_iterator main_it = vm->value.find("main");
    if (main_it == vm->value.end()) {
        return fail_vm(vm, "program is missing a main entrypoint");
    }

    set_vm_status(vm, VmStatus::running);
    clear_vm_terminal_fields(vm);

    CellPtr breakpoint_result = update_pc_or_break(vm, main_it->second);
    if (breakpoint_result) {
        return breakpoint_result;
    }

    CellPtr result = is_null_cell(main_it->second) ? main_it->second : evaluate_cell(main_it->second, vm);
    if (is_signal_cell(result)) {
        attach_terminal_state(vm, result);
        return result;
    }

    return result;
}

static shared_ptr<ScopeCell> current_frame(const shared_ptr<VmCell>& vm) {
    shared_ptr<VecCell> frames = vm_frames(vm);
    if (frames->value.empty()) {
        return nullptr;
    }

    const CellPtr frame_cell = frames->value.front();
    if (!frame_cell || frame_cell->type != Cell::Type::scope) {
        return nullptr;
    }

    return static_pointer_cast<ScopeCell>(frame_cell);
}

static const string* current_frame_name(const shared_ptr<ScopeCell>& frame) {
    const StrCell* name_cell = map_field_string(frame, "name");
    return name_cell ? &name_cell->value : nullptr;
}

static CellPtr resume_vm_frame(const shared_ptr<VmCell>& vm) {
    shared_ptr<ScopeCell> frame = current_frame(vm);
    if (!frame) {
        return nullptr;
    }

    const string* frame_name = current_frame_name(frame);
    if (!frame_name) {
        return fail_vm(vm, "VM frame is missing a string name");
    }

    if (*frame_name == "list") {
        return advance_list_frame(vm, frame);
    }

    return fail_vm(vm, "unknown VM frame type");
}

static CellPtr advance_vm(const shared_ptr<VmCell>& vm) {
    ensure_vm_state(vm);
    if (vm_is_terminal(vm)) {
        return vm_result(vm);
    }

    CellPtr result = nullptr;
    if (vm_frames(vm)->value.empty()) {
        result = start_vm_main(vm);
    }

    if (!vm_is_terminal(vm) && !vm_frames(vm)->value.empty()) {
        result = resume_vm_frame(vm);
    }

    if (vm_is_terminal(vm)) {
        return vm_result(vm);
    }

    if (vm_frames(vm)->value.empty()) {
        attach_terminal_state(vm, result);
        return result;
    }

    return result;
}

static CellPtr run_vm(const shared_ptr<VmCell>& vm) {
    while (!vm_is_terminal(vm)) {
        advance_vm(vm);
    }

    return vm_result(vm);
}

static void run_main(shared_ptr<VmCell> root_cell) {
    run_vm(root_cell);
}

int main(int argc, char* argv[]) {
    if (argc != 2) {
        std::cerr << "usage: ./helix <program.yaml>\n";
        return 1;
    }

    try {
        initialize_builtins(evaluate_cell, resolve_cell, advance_vm, render_show_output, make_error_cell);
        shared_ptr<ScopeCell> zygote = make_zygote();
        shared_ptr<VmCell> root_cell = load_root_cell_from_yaml_file(argv[1]);
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
