#include <core.hpp>

#include <algorithm>
#include <limits>
#include <utility>

Cell::Cell() : parent(nullptr) {}

Cell::Cell(Type initial_type) : type(initial_type), parent(nullptr) {}

Cell::Cell(const Cell&) = default;

Cell::Cell(Cell&&) = default;

Cell& Cell::operator=(const Cell&) = default;

Cell& Cell::operator=(Cell&&) = default;

Cell::~Cell() = default;

static void attach_parent_if_missing(const CellPtr& child, const CellPtr& parent) {
    if (!child || !parent || child->parent) {
        return;
    }

    child->parent = parent;
}

static void clear_existing_state(const shared_ptr<VmCell>& root_cell) {
    unordered_map<string, CellPtr>::iterator state_it = root_cell->value.find(CellField::state);
    if (state_it == root_cell->value.end() || !is_map_like_cell(state_it->second)) {
        return;
    }

    shared_ptr<MapCell> state = static_pointer_cast<MapCell>(state_it->second);
    state->clear_descendant_parent_links();
    state->value.clear();
    state->parent = nullptr;
}

CellPtr make_error_cell(const string& message, CellPtr value) {
    return make_shared<ErrCell>(message, move(value));
}

bool is_signal_cell(ConstCellPtr cell) {
    if (!cell) {
        return false;
    }

    return cell->type == Cell::Type::signal
        || cell->type == Cell::Type::return_signal
        || cell->type == Cell::Type::error_signal;
}

bool is_map_like_cell(ConstCellPtr cell) {
    if (!cell) {
        return false;
    }

    return cell->type == Cell::Type::map
        || cell->type == Cell::Type::scope
        || cell->type == Cell::Type::vm;
}

const char* cell_class_name(ConstCellPtr cell) {
    if (!cell) {
        return "null";
    }

    return cell->class_name;
}

const char* cell_class_name(const Cell* cell) {
    return cell_class_name(ConstCellPtr(const_cast<Cell*>(cell), [](const Cell*) {}));
}

CellPtr map_field_cell(ConstCellPtr map_cell, const string& key) {
    if (!is_map_like_cell(map_cell)) {
        return nullptr;
    }

    const auto& fields = static_cast<const MapCell&>(*map_cell).value;
    unordered_map<string, CellPtr>::const_iterator field_it = fields.find(key);
    if (field_it == fields.end()) {
        return nullptr;
    }

    return field_it->second;
}

static CellPtr make_path_error(const string& message, size_t segment_index, ConstCellPtr segment, ConstCellPtr receiver) {
    shared_ptr<MapCell> details = make_shared<MapCell>();
    details->set(CellField::kind, make_shared<StrCell>(CellValue::path_error));
    details->set(CellField::segment_index, make_shared<IntCell>(static_cast<int64_t>(segment_index)));
    details->set(CellField::receiver_type, make_shared<StrCell>(cell_class_name(receiver)));
    if (segment) {
        details->set(CellField::segment, const_pointer_cast<Cell>(segment));
    }
    return make_error_cell(message, details);
}

static CellPtr map_segment_for_child(const MapCell& parent, const Cell* child) {
    for (const auto& [key, candidate] : parent.value) {
        if (candidate.get() == child) {
            return make_shared<StrCell>(key);
        }
    }

    return nullptr;
}

static CellPtr vec_segment_for_child(const VecCell& parent, const Cell* child) {
    for (size_t index = 0; index < parent.value.size(); ++index) {
        if (parent.value[index].get() == child) {
            return make_shared<IntCell>(static_cast<int64_t>(index));
        }
    }

    return nullptr;
}

static CellPtr segment_for_child(const Cell* parent, const Cell* child) {
    if (is_map_like_cell(ConstCellPtr(const_cast<Cell*>(parent), [](const Cell*) {}))) {
        return map_segment_for_child(static_cast<const MapCell&>(*parent), child);
    }
    if (parent->type == Cell::Type::vec) {
        return vec_segment_for_child(static_cast<const VecCell&>(*parent), child);
    }

    return nullptr;
}

static bool append_reversed_path_segment(vector<CellPtr>& reversed_segments, const Cell*& current) {
    const Cell* parent = current->parent.get();
    if (!parent) {
        return false;
    }

    CellPtr segment = segment_for_child(parent, current);
    if (!segment) {
        return false;
    }

    reversed_segments.push_back(move(segment));
    current = parent;
    return true;
}

static bool path_segments_equal(ConstCellPtr left, ConstCellPtr right) {
    if (!left || !right || left->type != right->type) {
        return false;
    }

    if (left->type == Cell::Type::string) {
        return static_cast<const StrCell&>(*left).value == static_cast<const StrCell&>(*right).value;
    }

    if (left->type == Cell::Type::integer) {
        return static_cast<const IntCell&>(*left).value == static_cast<const IntCell&>(*right).value;
    }

    return false;
}

shared_ptr<VecCell> cell_path_from_root(const shared_ptr<VmCell>& root_cell, ConstCellPtr target_cell) {
    if (!root_cell || !target_cell) {
        return nullptr;
    }

    vector<CellPtr> reversed_segments;
    const Cell* current = target_cell.get();
    while (current && current != root_cell.get()) {
        if (!append_reversed_path_segment(reversed_segments, current)) {
            return nullptr;
        }
    }

    if (current != root_cell.get()) {
        return nullptr;
    }

    reverse(reversed_segments.begin(), reversed_segments.end());
    return make_shared<VecCell>(move(reversed_segments));
}

bool cell_paths_equal(ConstCellPtr left_path, ConstCellPtr right_path) {
    if (!left_path || !right_path || left_path->type != Cell::Type::vec || right_path->type != Cell::Type::vec) {
        return false;
    }

    const vector<CellPtr>& left_values = static_cast<const VecCell&>(*left_path).value;
    const vector<CellPtr>& right_values = static_cast<const VecCell&>(*right_path).value;
    if (left_values.size() != right_values.size()) {
        return false;
    }

    for (size_t index = 0; index < left_values.size(); ++index) {
        if (!path_segments_equal(left_values[index], right_values[index])) {
            return false;
        }
    }

    return true;
}

CellPtr cell_at_path(CellPtr root_cell, ConstCellPtr path_cell) {
    if (!root_cell) {
        return make_error_cell("object path requires a root cell");
    }
    if (!path_cell || path_cell->type != Cell::Type::vec) {
        return make_error_cell("object path must be a vector");
    }

    CellPtr current = move(root_cell);
    const VecCell& path = static_cast<const VecCell&>(*path_cell);
    for (size_t index = 0; index < path.value.size(); ++index) {
        ConstCellPtr segment = path.value[index];
        if (!segment) {
            return make_path_error("object path segment cannot be null", index, segment, current);
        }

        if (segment->type == Cell::Type::string) {
            if (!is_map_like_cell(current)) {
                return make_path_error("object path string segment requires a map-like receiver", index, segment, current);
            }

            const string& key = static_cast<const StrCell&>(*segment).value;
            CellPtr next = map_field_cell(current, key);
            if (!next) {
                return make_path_error("object path map key was not found", index, segment, current);
            }
            current = move(next);
            continue;
        }

        if (segment->type == Cell::Type::integer) {
            if (!current || current->type != Cell::Type::vec) {
                return make_path_error("object path integer segment requires a vector receiver", index, segment, current);
            }

            const int64_t offset = static_cast<const IntCell&>(*segment).value;
            const vector<CellPtr>& values = static_cast<const VecCell&>(*current).value;
            if (offset < 0 || static_cast<size_t>(offset) >= values.size()) {
                return make_path_error("object path vector offset is out of bounds", index, segment, current);
            }
            current = values[static_cast<size_t>(offset)];
            continue;
        }

        return make_path_error("object path segments must be strings or integers", index, segment, current);
    }

    return current;
}

shared_ptr<MapCell> map_field_map(ConstCellPtr map_cell, const string& key) {
    CellPtr field = map_field_cell(map_cell, key);
    if (!is_map_like_cell(field)) {
        return nullptr;
    }

    return static_pointer_cast<MapCell>(field);
}

shared_ptr<ScopeCell> map_field_scope(ConstCellPtr map_cell, const string& key) {
    CellPtr field = map_field_cell(map_cell, key);
    if (!field || field->type != Cell::Type::scope) {
        return nullptr;
    }

    return static_pointer_cast<ScopeCell>(field);
}

shared_ptr<VecCell> map_field_vec(ConstCellPtr map_cell, const string& key) {
    CellPtr field = map_field_cell(map_cell, key);
    if (!field || field->type != Cell::Type::vec) {
        return nullptr;
    }

    return static_pointer_cast<VecCell>(field);
}

const StrCell* map_field_string(ConstCellPtr map_cell, const string& key) {
    CellPtr field = map_field_cell(map_cell, key);
    if (!field || field->type != Cell::Type::string) {
        return nullptr;
    }

    return &static_cast<const StrCell&>(*field);
}

const IntCell* map_field_int(ConstCellPtr map_cell, const string& key) {
    CellPtr field = map_field_cell(map_cell, key);
    if (!field || field->type != Cell::Type::integer) {
        return nullptr;
    }

    return &static_cast<const IntCell&>(*field);
}

bool is_typed_slot(ConstCellPtr cell) {
    const StrCell* type_cell = map_field_string(cell, CellField::type);
    return type_cell
        && type_cell->value != CellValue::function
        && map_field_cell(cell, CellField::value);
}

CellPtr typed_slot_value(ConstCellPtr cell) {
    if (!is_typed_slot(cell)) {
        return nullptr;
    }

    return map_field_cell(cell, CellField::value);
}

static CellPtr make_type_error_cell(const string& message, const string& expected_type, ConstCellPtr actual_value) {
    shared_ptr<MapCell> details = make_shared<MapCell>();
    details->set(CellField::kind, make_shared<StrCell>(CellValue::type_error));
    details->set(CellField::expected_type, make_shared<StrCell>(expected_type));
    details->set(CellField::actual_type, make_shared<StrCell>(cell_class_name(actual_value)));
    return make_error_cell(message, details);
}

static CellPtr validate_i32_value(ConstCellPtr value) {
    if (!value || value->type != Cell::Type::integer) {
        return make_type_error_cell("i32 typed slot expects an integer value", "i32", value);
    }

    const int64_t integer = static_cast<const IntCell&>(*value).value;
    if (integer < numeric_limits<int32_t>::min() || integer > numeric_limits<int32_t>::max()) {
        return make_type_error_cell("i32 typed slot value is out of range", "i32", value);
    }

    return nullptr;
}

static CellPtr validate_i64_value(ConstCellPtr value) {
    if (!value || value->type != Cell::Type::integer) {
        return make_type_error_cell("i64 typed slot expects an integer value", "i64", value);
    }

    return nullptr;
}

static CellPtr validate_typed_slot_value(ConstCellPtr slot_cell, ConstCellPtr value) {
    const StrCell* type_cell = map_field_string(slot_cell, CellField::type);
    if (!type_cell) {
        return make_error_cell("typed slot is missing a type");
    }

    if (type_cell->value == "i32") {
        return validate_i32_value(value);
    }
    if (type_cell->value == "i64") {
        return validate_i64_value(value);
    }

    return make_error_cell("unsupported typed slot type: " + type_cell->value);
}

CellPtr set_typed_slot_value(const shared_ptr<MapCell>& slot_cell, CellPtr value) {
    if (!is_typed_slot(slot_cell)) {
        return make_error_cell("set target is not a typed slot");
    }

    CellPtr error = validate_typed_slot_value(slot_cell, value);
    if (error) {
        return error;
    }

    slot_cell->set(CellField::value, move(value));
    return nullptr;
}

shared_ptr<MapCell> expect_map_cell(CellPtr cell) {
    if (is_map_like_cell(cell)) {
        return static_pointer_cast<MapCell>(move(cell));
    }

    return nullptr;
}

shared_ptr<ScopeCell> expect_scope_cell(CellPtr cell) {
    if (cell && cell->type == Cell::Type::scope) {
        return static_pointer_cast<ScopeCell>(move(cell));
    }

    return nullptr;
}

shared_ptr<VmCell> expect_vm_cell(CellPtr cell) {
    if (cell && cell->type == Cell::Type::vm) {
        return static_pointer_cast<VmCell>(move(cell));
    }

    return nullptr;
}

const char* vm_status_name(VmStatus status) {
    switch (status) {
    case VmStatus::ready:
        return CellValue::ready;
    case VmStatus::running:
        return CellValue::running;
    case VmStatus::finished:
        return CellValue::finished;
    case VmStatus::error:
        return CellValue::error;
    case VmStatus::signaled:
        return CellValue::signaled;
    }

    return CellValue::ready;
}

shared_ptr<MapCell> ensure_vm_state(const shared_ptr<VmCell>& vm) {
    shared_ptr<MapCell> state = map_field_map(vm, CellField::state);
    if (!state) {
        state = make_shared<MapCell>();
        vm->set(CellField::state, state);
    }

    if (!map_field_string(state, CellField::status)) {
        state->set(CellField::status, make_shared<StrCell>(vm_status_name(VmStatus::ready)));
    }

    if (!map_field_vec(state, CellField::frames)) {
        state->set(CellField::frames, make_shared<VecCell>());
    }

    return state;
}

CellPtr vm_status_cell(const shared_ptr<VmCell>& vm) {
    return map_field_cell(ensure_vm_state(vm), CellField::status);
}

VmStatus vm_status(const shared_ptr<VmCell>& vm) {
    const StrCell* status_cell = map_field_string(ensure_vm_state(vm), CellField::status);
    if (!status_cell) {
        return VmStatus::ready;
    }

    const string& status = status_cell->value;
    if (status == CellValue::running) {
        return VmStatus::running;
    }
    if (status == CellValue::finished) {
        return VmStatus::finished;
    }
    if (status == CellValue::error) {
        return VmStatus::error;
    }
    if (status == CellValue::signaled) {
        return VmStatus::signaled;
    }

    return VmStatus::ready;
}

shared_ptr<VecCell> vm_frames(const shared_ptr<VmCell>& vm) {
    return map_field_vec(ensure_vm_state(vm), CellField::frames);
}

shared_ptr<VecCell> vm_breakpoints(const shared_ptr<VmCell>& vm) {
    shared_ptr<VecCell> breakpoints = map_field_vec(vm, CellField::breakpoints);
    if (!breakpoints) {
        breakpoints = make_shared<VecCell>();
        vm->set(CellField::breakpoints, breakpoints);
    }

    return breakpoints;
}

void set_vm_status(const shared_ptr<VmCell>& vm, VmStatus status) {
    ensure_vm_state(vm)->set(CellField::status, make_shared<StrCell>(vm_status_name(status)));
}

void clear_vm_terminal_fields(const shared_ptr<VmCell>& vm) {
    shared_ptr<MapCell> state = ensure_vm_state(vm);
    state->value.erase(CellField::result);
    state->value.erase(CellField::error);
}

bool vm_is_terminal(const shared_ptr<VmCell>& vm) {
    VmStatus status = vm_status(vm);
    return status == VmStatus::finished
        || status == VmStatus::error
        || status == VmStatus::signaled;
}

CellPtr vm_result(const shared_ptr<VmCell>& vm) {
    return map_field_cell(ensure_vm_state(vm), CellField::result);
}

bool arm_list_frame(const shared_ptr<VmCell>& vm, CellPtr sequence_cell, int64_t start_index) {
    shared_ptr<VecCell> sequence_path = cell_path_from_root(vm, sequence_cell);
    if (!sequence_path) {
        return false;
    }
    sequence_path->append(make_shared<IntCell>(start_index));

    shared_ptr<MapCell> state = ensure_vm_state(vm);
    shared_ptr<VecCell> frames = make_shared<VecCell>();
    frames->append(sequence_path);
    state->set(CellField::frames, frames);
    state->value.erase("pc");
    set_vm_status(vm, VmStatus::running);
    clear_vm_terminal_fields(vm);
    return true;
}

shared_ptr<MapCell> make_finished_state_cell() {
    shared_ptr<MapCell> state = make_shared<MapCell>();
    state->set(CellField::status, make_shared<StrCell>(vm_status_name(VmStatus::finished)));
    state->set(CellField::frames, make_shared<VecCell>());
    return state;
}

void attach_finished_state(const shared_ptr<VmCell>& root_cell, CellPtr result) {
    clear_existing_state(root_cell);
    shared_ptr<MapCell> state = make_finished_state_cell();
    if (result) {
        state->set(CellField::result, move(result));
    }
    root_cell->set(CellField::state, state);
}

void attach_terminal_state(const shared_ptr<VmCell>& root_cell, CellPtr result) {
    if (!result) {
        attach_finished_state(root_cell, nullptr);
        return;
    }

    if (result->type == Cell::Type::error_signal) {
        clear_existing_state(root_cell);
        shared_ptr<MapCell> state = make_shared<MapCell>();
        state->set(CellField::status, make_shared<StrCell>(vm_status_name(VmStatus::error)));
        state->set(CellField::frames, make_shared<VecCell>());

        const ErrCell& error_cell = static_cast<const ErrCell&>(*result);
        state->set(CellField::error, make_shared<StrCell>(error_cell.message));
        state->set(CellField::result, result);
        root_cell->set(CellField::state, state);
        return;
    }

    if (is_signal_cell(result)) {
        clear_existing_state(root_cell);
        shared_ptr<MapCell> state = make_shared<MapCell>();
        state->set(CellField::status, make_shared<StrCell>(vm_status_name(VmStatus::signaled)));
        state->set(CellField::frames, make_shared<VecCell>());
        state->set(CellField::result, move(result));
        root_cell->set(CellField::state, state);
        return;
    }

    attach_finished_state(root_cell, move(result));
}

CellPtr expect_form_arity(size_t actual_arity, size_t expected_arity, const char* who) {
    if (actual_arity == expected_arity) {
        return nullptr;
    }

    return make_error_cell(string(who) + " expects exactly " + to_string(expected_arity - 1) + " argument"
        + (expected_arity == 2 ? "" : "s"));
}

CellPtr expect_int_cell(ConstCellPtr cell, const char* who) {
    if (is_signal_cell(cell)) {
        return const_pointer_cast<Cell>(cell);
    }

    if (!cell || cell->type != Cell::Type::integer) {
        return make_error_cell(string(who) + " expects integer arguments");
    }

    return const_pointer_cast<Cell>(cell);
}

static CellPtr make_lookup_error(
    const string& message,
    const string& queried_name,
    const Cell* receiver,
    const string& failed_segment,
    const string& resolved_prefix);

static CellPtr lookup_path_from_receiver(
    const Cell* receiver,
    const string& name,
    const shared_ptr<VmCell>& root_cell) {
    if (!receiver) {
        return nullptr;
    }

    const Cell* current_receiver = receiver;
    size_t segment_start = 0;
    string resolved_prefix {};

    while (segment_start < name.size()) {
        size_t dot_index = name.find('.', segment_start);
        string segment = name.substr(segment_start, dot_index - segment_start);
        if (segment.empty()) {
            return make_lookup_error("lookup failed because the path contains an empty segment", name, current_receiver, segment, resolved_prefix);
        }

        CellPtr resolved = current_receiver->lookup_member(segment, root_cell);
        if (resolved && resolved->type == Cell::Type::error_signal) {
            return resolved;
        }
        if (!resolved) {
            return make_lookup_error(
                "lookup failed because the requested member was not found",
                name,
                current_receiver,
                segment,
                resolved_prefix);
        }

        if (dot_index == string::npos) {
            return resolved;
        }

        resolved_prefix = resolved_prefix.empty() ? segment : resolved_prefix + "." + segment;
        current_receiver = resolved.get();
        segment_start = dot_index + 1;
    }

    return nullptr;
}

static CellPtr make_lookup_error(
    const string& message,
    const string& queried_name,
    const Cell* receiver,
    const string& failed_segment,
    const string& resolved_prefix) {
    unordered_map<string, CellPtr> fields;
    fields[CellField::kind] = make_shared<StrCell>(CellValue::lookup_error);
    fields[CellField::name] = make_shared<StrCell>(queried_name);
    fields[CellField::receiver_type] = make_shared<StrCell>(cell_class_name(receiver));
    if (!failed_segment.empty()) {
        fields[CellField::failed_segment] = make_shared<StrCell>(failed_segment);
    }
    if (!resolved_prefix.empty()) {
        fields[CellField::resolved_prefix] = make_shared<StrCell>(resolved_prefix);
    }
    return make_error_cell(message, make_shared<MapCell>(move(fields)));
}

size_t Cell::size() const noexcept {
    return static_cast<size_t>(-1);
}

CellPtr Cell::call(const vector<CellPtr>&, CellPtr) const {
    return make_error_cell("Cell is not callable");
}

CellPtr Cell::lookup(const string& name, const shared_ptr<VmCell>& root_cell) const {
    return make_error_cell("lookup is not implemented for this cell");
}

CellPtr Cell::lookup_member(const string& name, const shared_ptr<VmCell>& root_cell) const {
    return make_error_cell("member lookup is not implemented for this cell");
}

void Cell::clear_descendant_parent_links() {}

MapCell::MapCell() : Cell(Type::map) {
    class_name = "MapCell";
}

MapCell::MapCell(unordered_map<string, CellPtr> fields)
    : Cell(Type::map), value(move(fields)) {
    class_name = "MapCell";
}

void MapCell::set(const string& key, CellPtr child) {
    attach_parent_if_missing(child, shared_from_this());
    value[key] = move(child);
}

size_t MapCell::size() const noexcept {
    return value.size();
}

CellPtr MapCell::lookup_member(const string& name, const shared_ptr<VmCell>&) const {
    unordered_map<string, CellPtr>::const_iterator field_it = value.find(name);
    if (field_it == value.end()) {
        return nullptr;
    }

    if (is_typed_slot(field_it->second)) {
        return typed_slot_value(field_it->second);
    }

    return field_it->second;
}

static CellPtr lookup_from_scope_like(const Cell* start, const string& name, const shared_ptr<VmCell>& root_cell) {
    CellPtr first_error = nullptr;

    for (const Cell* current = start; current; current = current->parent.get()) {
        CellPtr resolved = lookup_path_from_receiver(current, name, root_cell);
        if (resolved && resolved->type == Cell::Type::error_signal) {
            if (!first_error) {
                first_error = resolved;
            }
            continue;
        }
        if (resolved) {
            return resolved;
        }
    }

    if (root_cell && root_cell.get() != start) {
        CellPtr resolved = lookup_path_from_receiver(root_cell.get(), name, root_cell);
        if (resolved && resolved->type == Cell::Type::error_signal) {
            if (!first_error) {
                first_error = resolved;
            }
        } else if (resolved) {
            return resolved;
        }
    }

    return first_error;
}

ScopeCell::ScopeCell() : MapCell() {
    type = Type::scope;
    class_name = "ScopeCell";
}

ScopeCell::ScopeCell(unordered_map<string, CellPtr> fields)
    : MapCell(move(fields)) {
    type = Type::scope;
    class_name = "ScopeCell";
}

CellPtr ScopeCell::lookup(const string& name, const shared_ptr<VmCell>& root_cell) const {
    return lookup_from_scope_like(this, name, root_cell);
}

VmCell::VmCell() : MapCell() {
    type = Type::vm;
    class_name = "VmCell";
}

VmCell::VmCell(unordered_map<string, CellPtr> fields)
    : MapCell(move(fields)) {
    type = Type::vm;
    class_name = "VmCell";
}

CellPtr VmCell::lookup(const string& name, const shared_ptr<VmCell>& root_cell) const {
    return lookup_from_scope_like(this, name, root_cell);
}

void MapCell::clear_descendant_parent_links() {
    for (auto& [_, child] : value) {
        if (!child) {
            continue;
        }

        child->parent = nullptr;
        child->clear_descendant_parent_links();
    }
}

VecCell::VecCell() : Cell(Type::vec) {
    class_name = "VecCell";
}

VecCell::VecCell(vector<CellPtr> elements)
    : Cell(Type::vec), value(move(elements)) {
    class_name = "VecCell";
}

void VecCell::append(CellPtr child) {
    attach_parent_if_missing(child, shared_from_this());
    value.push_back(move(child));
}

size_t VecCell::size() const noexcept {
    return value.size();
}

void VecCell::clear_descendant_parent_links() {
    for (CellPtr& child : value) {
        if (!child) {
            continue;
        }

        child->parent = nullptr;
        child->clear_descendant_parent_links();
    }
}

IntCell::IntCell(int64_t initial_value)
    : Cell(Type::integer), value(initial_value) {
    class_name = "IntCell";
}

StrCell::StrCell() : Cell(Type::string) {
    class_name = "StrCell";
}

StrCell::StrCell(string initial_value)
    : Cell(Type::string), value(move(initial_value)) {
    class_name = "StrCell";
}

size_t StrCell::size() const noexcept {
    return value.size();
}

NilCell::NilCell() : Cell(Type::nil) {
    class_name = "NilCell";
}

FunCell::FunCell() : Cell(Type::function) {
    class_name = "FunCell";
}

FunCell::FunCell(Implementation implementation)
    : Cell(Type::function), value(move(implementation)) {
    class_name = "FunCell";
}

CellPtr FunCell::call(const vector<CellPtr>& arguments, CellPtr current_vm) const {
    if (!value) {
        return make_error_cell("function cell has no implementation");
    }
    return value(arguments, move(current_vm));
}

SigCell::SigCell() : Cell(Type::signal) {
    class_name = "SigCell";
}

SigCell::SigCell(CellPtr initial_value)
    : Cell(Type::signal), value(move(initial_value)) {
    class_name = "SigCell";
}

SigCell::SigCell(Type initial_type, CellPtr initial_value)
    : Cell(initial_type), value(move(initial_value)) {
    class_name = "SigCell";
}

void SigCell::clear_descendant_parent_links() {
    if (!value) {
        return;
    }

    value->parent = nullptr;
    value->clear_descendant_parent_links();
}

RetCell::RetCell() : SigCell(Type::return_signal, nullptr) {
    class_name = "RetCell";
}

RetCell::RetCell(CellPtr initial_value)
    : SigCell(Type::return_signal, move(initial_value)) {
    class_name = "RetCell";
}

ErrCell::ErrCell() : SigCell(Type::error_signal, nullptr) {
    class_name = "ErrCell";
}

ErrCell::ErrCell(string initial_message, CellPtr initial_value)
    : SigCell(Type::error_signal, move(initial_value)), message(move(initial_message)) {
    class_name = "ErrCell";
}
