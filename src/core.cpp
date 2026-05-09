#include <core.hpp>

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

static void clear_existing_state(const shared_ptr<MapCell>& root_cell) {
    unordered_map<string, CellPtr>::iterator state_it = root_cell->value.find("state");
    if (state_it == root_cell->value.end() || !state_it->second || state_it->second->type != Cell::Type::map) {
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

CellPtr map_field_cell(ConstCellPtr map_cell, const string& key) {
    if (!map_cell || map_cell->type != Cell::Type::map) {
        return nullptr;
    }

    const auto& fields = static_cast<const MapCell&>(*map_cell).value;
    unordered_map<string, CellPtr>::const_iterator field_it = fields.find(key);
    if (field_it == fields.end()) {
        return nullptr;
    }

    return field_it->second;
}

shared_ptr<MapCell> map_field_map(ConstCellPtr map_cell, const string& key) {
    CellPtr field = map_field_cell(map_cell, key);
    if (!field || field->type != Cell::Type::map) {
        return nullptr;
    }

    return static_pointer_cast<MapCell>(field);
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

shared_ptr<MapCell> expect_map_cell(CellPtr cell, const char* who) {
    (void)who;

    if (cell && cell->type == Cell::Type::map) {
        return static_pointer_cast<MapCell>(move(cell));
    }

    return nullptr;
}

shared_ptr<MapCell> make_finished_state_cell() {
    shared_ptr<MapCell> state = make_shared<MapCell>();
    state->set("status", make_shared<StrCell>("finished"));
    state->set("frames", make_shared<VecCell>());
    return state;
}

void attach_finished_state(const shared_ptr<MapCell>& root_cell, CellPtr result) {
    clear_existing_state(root_cell);
    shared_ptr<MapCell> state = make_finished_state_cell();
    if (result) {
        state->set("result", move(result));
    }
    root_cell->set("state", state);
}

void attach_terminal_state(const shared_ptr<MapCell>& root_cell, CellPtr result) {
    if (!result) {
        attach_finished_state(root_cell, nullptr);
        return;
    }

    if (result->type == Cell::Type::error_signal) {
        clear_existing_state(root_cell);
        shared_ptr<MapCell> state = make_shared<MapCell>();
        state->set("status", make_shared<StrCell>("error"));
        state->set("frames", make_shared<VecCell>());

        const ErrCell& error_cell = static_cast<const ErrCell&>(*result);
        state->set("error", make_shared<StrCell>(error_cell.message));
        root_cell->set("state", state);
        return;
    }

    if (is_signal_cell(result)) {
        clear_existing_state(root_cell);
        shared_ptr<MapCell> state = make_shared<MapCell>();
        state->set("status", make_shared<StrCell>("signaled"));
        state->set("frames", make_shared<VecCell>());
        state->set("result", move(result));
        root_cell->set("state", state);
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

size_t Cell::size() const noexcept {
    return static_cast<size_t>(-1);
}

CellPtr Cell::call(const vector<CellPtr>&, CellPtr) const {
    return make_error_cell("Cell is not callable");
}

void Cell::clear_descendant_parent_links() {}

MapCell::MapCell() : Cell(Type::map) {}

MapCell::MapCell(unordered_map<string, CellPtr> fields)
    : Cell(Type::map), value(move(fields)) {}

void MapCell::set(const string& key, CellPtr child) {
    attach_parent_if_missing(child, shared_from_this());
    value[key] = move(child);
}

size_t MapCell::size() const noexcept {
    return value.size();
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

VecCell::VecCell() : Cell(Type::vec) {}

VecCell::VecCell(vector<CellPtr> elements)
    : Cell(Type::vec), value(move(elements)) {}

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
    : Cell(Type::integer), value(initial_value) {}

StrCell::StrCell() : Cell(Type::string) {}

StrCell::StrCell(string initial_value)
    : Cell(Type::string), value(move(initial_value)) {}

size_t StrCell::size() const noexcept {
    return value.size();
}

NilCell::NilCell() : Cell(Type::nil) {}

FunCell::FunCell() : Cell(Type::function) {
}

FunCell::FunCell(Implementation implementation)
    : Cell(Type::function), value(move(implementation)) {}

CellPtr FunCell::call(const vector<CellPtr>& arguments, CellPtr current_vm) const {
    if (!value) {
        return make_error_cell("function cell has no implementation");
    }
    return value(arguments, move(current_vm));
}

SigCell::SigCell() : Cell(Type::signal) {}

SigCell::SigCell(CellPtr initial_value)
    : Cell(Type::signal), value(move(initial_value)) {}

SigCell::SigCell(Type initial_type, CellPtr initial_value)
    : Cell(initial_type), value(move(initial_value)) {}

void SigCell::clear_descendant_parent_links() {
    if (!value) {
        return;
    }

    value->parent = nullptr;
    value->clear_descendant_parent_links();
}

RetCell::RetCell() : SigCell(Type::return_signal, nullptr) {}

RetCell::RetCell(CellPtr initial_value)
    : SigCell(Type::return_signal, move(initial_value)) {}

ErrCell::ErrCell() : SigCell(Type::error_signal, nullptr) {}

ErrCell::ErrCell(string initial_message, CellPtr initial_value)
    : SigCell(Type::error_signal, move(initial_value)), message(move(initial_message)) {}
