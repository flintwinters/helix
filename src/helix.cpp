#include <algorithm>
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
    ensure_vm_state(vm)->value.erase(CellField::yield_reason);
}

static CellPtr breakpoint_error(const string& message, ConstCellPtr source) {
    shared_ptr<MapCell> details = make_shared<MapCell>();
    details->set(CellField::type, make_shared<StrCell>(CellValue::breakpoint_error));
    if (source) {
        details->set(CellField::source, const_pointer_cast<Cell>(source));
        attach_source_location(details, source);
    }
    return make_error_cell(message, details);
}

static bool vm_breakpoint_matches(const shared_ptr<VmCell>& vm, ConstCellPtr pc, CellPtr& error) {
    CellPtr breakpoints_cell = map_field_cell(vm, CellField::breakpoints);
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

static void store_vm_frame(const shared_ptr<VmCell>& vm, CellPtr pc) {
    shared_ptr<MapCell> state = ensure_vm_state(vm);
    shared_ptr<VecCell> frames = make_shared<VecCell>();
    frames->append(move(pc));
    CellPtr old_frames = map_field_cell(state, CellField::frames);
    if (old_frames) {
        old_frames->clear_descendant_parent_links();
        old_frames->parent = nullptr;
    }
    state->set(CellField::frames, frames);
    state->value.erase(CellField::pc);
}

static CellPtr yield_at_breakpoint(const shared_ptr<VmCell>& vm, CellPtr pc) {
    shared_ptr<MapCell> state = ensure_vm_state(vm);
    store_vm_frame(vm, move(pc));
    state->set(CellField::yield_reason, make_shared<StrCell>(CellValue::breakpoint));
    set_vm_status(vm, VmStatus::running);
    clear_vm_terminal_fields(vm);
    return vm_status_cell(vm);
}

static shared_ptr<MapCell> make_resolution_details(
    const string& type,
    const string& name,
    const Cell* context,
    CellPtr source = nullptr) {
    shared_ptr<MapCell> details = make_shared<MapCell>();
    details->set(CellField::type, make_shared<StrCell>(type));
    details->set(CellField::name, make_shared<StrCell>(name));
    details->set(CellField::context_type, make_shared<StrCell>(cell_class_name(context)));
    if (source) {
        details->set(CellField::source, source);
        attach_source_location(details, source);
    }
    return details;
}

static void attach_error_source(ErrCell& error, CellPtr source) {
    if (!source) {
        return;
    }

    shared_ptr<MapCell> details = make_shared<MapCell>();
    attach_source_location(details, source);
    details->set(CellField::source, move(source));
    absorb_error_details(error, move(details));
}

static bool has_error_details(const ErrCell& error) {
    if (error.value) {
        return true;
    }

    return any_of(
        error.details.fields.begin(),
        error.details.fields.end(),
        [](const auto& field) { return field.first != CellField::debug; });
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
        if (!has_error_details(error)) {
            shared_ptr<MapCell> details = make_resolution_details(CellValue::resolution_error, name, context, node);
            absorb_error_details(error, move(details));
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
            make_resolution_details(CellValue::unresolved_actor, actor_name, root_cell.get(), form.value.front()));
    }

    if (!actor || actor->type != Cell::Type::function) {
        shared_ptr<MapCell> details = make_shared<MapCell>();
        details->set(CellField::type, make_shared<StrCell>(CellValue::invalid_actor));
        details->set(CellField::actor_type, make_shared<StrCell>(cell_class_name(actor)));
        details->set(CellField::source, form.value.front());
        attach_source_location(details, form.value.front());
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

static void clear_vm_frames(const shared_ptr<VmCell>& vm) {
    shared_ptr<MapCell> state = ensure_vm_state(vm);
    CellPtr old_frames = map_field_cell(state, CellField::frames);
    if (old_frames) {
        old_frames->clear_descendant_parent_links();
        old_frames->parent = nullptr;
    }
    state->set(CellField::frames, make_shared<VecCell>());
    state->value.erase(CellField::pc);
}

static shared_ptr<VecCell> next_vector_path(ConstCellPtr pc, size_t next_index) {
    shared_ptr<VecCell> next_path = clone_path_prefix(pc);
    if (!next_path) {
        return nullptr;
    }

    next_path->append(make_shared<IntCell>(static_cast<int64_t>(next_index)));
    return next_path;
}

static void store_resume_path(const shared_ptr<VmCell>& vm, CellPtr next_pc) {
    store_vm_frame(vm, move(next_pc));
    set_vm_status(vm, VmStatus::running);
    clear_vm_terminal_fields(vm);
}

static optional<size_t> path_tail_index(ConstCellPtr pc) {
    if (!pc || pc->type != Cell::Type::vec) {
        return nullopt;
    }

    const vector<CellPtr>& path = static_cast<const VecCell&>(*pc).value;
    if (path.empty()) {
        return nullopt;
    }

    ConstCellPtr tail = path.back();
    if (!tail || tail->type != Cell::Type::integer) {
        return nullopt;
    }

    int64_t index = static_cast<const IntCell&>(*tail).value;
    if (index < 0) {
        return nullopt;
    }

    return static_cast<size_t>(index);
}

static shared_ptr<VecCell> frame_sequence(const shared_ptr<VmCell>& vm, ConstCellPtr pc) {
    shared_ptr<VecCell> sequence_path = clone_path_prefix(pc);
    if (!sequence_path) {
        return nullptr;
    }

    CellPtr sequence = cell_at_path(vm, sequence_path);
    if (!sequence || sequence->type != Cell::Type::vec) {
        return nullptr;
    }

    return static_pointer_cast<VecCell>(sequence);
}

static optional<size_t> validated_pc_index(
    const shared_ptr<VmCell>& vm,
    const shared_ptr<VecCell>& sequence,
    ConstCellPtr pc) {
    optional<size_t> index = path_tail_index(pc);
    if (!index) {
        fail_vm(vm, "VM frame path must end with an integer vector offset");
        return nullopt;
    }

    if (*index >= sequence->value.size()) {
        fail_vm(vm, "VM frame vector offset is out of bounds");
        return nullopt;
    }

    return index;
}

static bool current_frame_is(const shared_ptr<VmCell>& vm, ConstCellPtr pc) {
    shared_ptr<VecCell> frames = vm_frames(vm);
    return frames->value.size() == 1 && cell_paths_equal(frames->value.front(), pc);
}

static bool advance_vector_frame(const shared_ptr<VmCell>& vm, ConstCellPtr pc, size_t current_index, size_t sequence_size) {
    size_t next_index = current_index + 1;
    if (next_index < sequence_size) {
        store_resume_path(vm, next_vector_path(pc, next_index));
        return true;
    }

    clear_vm_frames(vm);
    return false;
}

static CellPtr resume_vector_frame(const shared_ptr<VmCell>& vm, ConstCellPtr pc) {
    shared_ptr<VecCell> sequence = frame_sequence(vm, pc);
    if (!sequence) {
        return fail_vm(vm, "VM frame path must resolve inside a vector");
    }

    optional<size_t> current_index = validated_pc_index(vm, sequence, pc);
    if (!current_index) {
        return vm_result(vm);
    }

    CellPtr value = evaluate_cell(sequence->value[*current_index], vm);
    if (is_signal_cell(value)) {
        attach_terminal_state(vm, value);
        return value;
    }

    if (current_frame_is(vm, pc)) {
        if (!advance_vector_frame(vm, pc, *current_index, sequence->value.size())) {
            return make_shared<NilCell>();
        }
    }

    return nullptr;
}

static CellPtr resume_direct_frame(const shared_ptr<VmCell>& vm, ConstCellPtr pc) {
    CellPtr node = cell_at_path(vm, pc);
    if (!node || is_signal_cell(node)) {
        return node ? node : fail_vm(vm, "VM frame path could not be resolved");
    }

    CellPtr value = evaluate_cell(node, vm);
    if (is_signal_cell(value)) {
        attach_terminal_state(vm, value);
        return value;
    }

    if (current_frame_is(vm, pc)) {
        clear_vm_frames(vm);
    }

    return value;
}

static CellPtr start_vm_main(const shared_ptr<VmCell>& vm) {
    unordered_map<string, CellPtr>::const_iterator main_it = vm->value.find(CellField::main);
    if (main_it == vm->value.end()) {
        return fail_vm(vm, "program is missing a main entrypoint");
    }

    set_vm_status(vm, VmStatus::running);
    clear_vm_terminal_fields(vm);

    shared_ptr<VecCell> main_pc = make_shared<VecCell>();
    main_pc->value.push_back(make_shared<StrCell>(CellField::main));
    CellPtr error = nullptr;
    if (vm_breakpoint_matches(vm, main_pc, error)) {
        return yield_at_breakpoint(vm, main_pc);
    }
    if (error) {
        attach_terminal_state(vm, error);
        return error;
    }
    clear_vm_yield_reason(vm);

    CellPtr result = is_null_cell(main_it->second) ? main_it->second : evaluate_cell(main_it->second, vm);
    if (is_signal_cell(result)) {
        attach_terminal_state(vm, result);
        return result;
    }

    return result;
}

static shared_ptr<VecCell> current_frame(const shared_ptr<VmCell>& vm) {
    shared_ptr<VecCell> frames = vm_frames(vm);
    if (frames->value.empty()) {
        return nullptr;
    }

    const CellPtr frame_cell = frames->value.front();
    if (!frame_cell || frame_cell->type != Cell::Type::vec) {
        return nullptr;
    }

    return static_pointer_cast<VecCell>(frame_cell);
}

static CellPtr resume_vm_frame(const shared_ptr<VmCell>& vm) {
    shared_ptr<VecCell> pc = current_frame(vm);
    if (!pc) {
        return nullptr;
    }

    CellPtr error = nullptr;
    if (vm_breakpoint_matches(vm, pc, error)) {
        return yield_at_breakpoint(vm, pc);
    }
    if (error) {
        attach_terminal_state(vm, error);
        return error;
    }
    clear_vm_yield_reason(vm);

    return path_tail_index(pc)
        ? resume_vector_frame(vm, pc)
        : resume_direct_frame(vm, pc);
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
        initialize_builtins(evaluate_cell, resolve_cell, advance_vm, render_show_output, make_error_cell_at);
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
