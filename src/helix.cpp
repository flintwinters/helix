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

static CellPtr fail_vm(const shared_ptr<VmCell>& vm, const string& message) {
    CellPtr error = make_error_cell(message);
    attach_terminal_state(vm, error);
    return error;
}

static CellPtr evaluate_cell(CellPtr node, const shared_ptr<VmCell>& root_cell);

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
        if (!error.value || error.value->type != Cell::Type::map) {
            error.value = make_resolution_details("resolution_error", name, context, node);
        } else {
            static_pointer_cast<MapCell>(error.value)->set("source", node);
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

static void store_list_resume_frame(const shared_ptr<VmCell>& vm, const shared_ptr<ScopeCell>& frame, size_t next_index) {
    frame->set("index", make_shared<IntCell>(static_cast<int64_t>(next_index)));
    ensure_vm_state(vm)->set("frames", make_shared<VecCell>(vector<CellPtr> {frame}));
    set_vm_status(vm, VmStatus::running);
    clear_vm_terminal_fields(vm);
}

static void retire_unyielded_frame(const shared_ptr<ScopeCell>& frame) {
    frame->clear_descendant_parent_links();
    frame->value.clear();
    frame->parent = nullptr;
}

static CellPtr advance_list_frame(const shared_ptr<VmCell>& vm, const shared_ptr<ScopeCell>& frame) {
    shared_ptr<VecCell> sequence = frame_values(frame);
    if (!sequence) {
        return fail_vm(vm, "list frame is missing a vector sequence");
    }

    const IntCell* index_cell = frame_index(frame);
    if (!index_cell) {
        return fail_vm(vm, "list frame is missing an integer index");
    }

    int64_t index = index_cell->value;
    if (index < 0 || static_cast<size_t>(index) >= sequence->value.size()) {
        return fail_vm(vm, "list frame index is out of bounds");
    }

    ensure_vm_state(vm)->set("frames", make_shared<VecCell>());
    bool yielded = false;

    for (size_t current_index = static_cast<size_t>(index); current_index < sequence->value.size(); ++current_index) {
        CellPtr value = evaluate_cell(sequence->value[current_index], vm);
        if (is_signal_cell(value)) {
            attach_terminal_state(vm, value);
            return value;
        }

        size_t next_index = current_index + 1;
        if (next_index < sequence->value.size()) {
            yielded = true;
            store_list_resume_frame(vm, frame, next_index);
        }
    }

    if (!yielded) {
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
