#include <core.hpp>

#include <stdexcept>
#include <utility>

Cell::Cell() : parent(nullptr) {}

Cell::Cell(Type initial_type) : type(initial_type), parent(nullptr) {}

Cell::Cell(const Cell&) = default;

Cell::Cell(Cell&&) = default;

Cell& Cell::operator=(const Cell&) = default;

Cell& Cell::operator=(Cell&&) = default;

Cell::~Cell() = default;

CellPtr make_error_cell(const string& message, CellPtr value) {
    return make_shared<ErrCell>(message, move(value));
}

CellPtr expect_form_arity(size_t actual_arity, size_t expected_arity, const char* who) {
    if (actual_arity == expected_arity) {
        return nullptr;
    }

    return make_error_cell(string(who) + " expects exactly " + to_string(expected_arity - 1) + " argument"
        + (expected_arity == 2 ? "" : "s"));
}

CellPtr expect_int_cell(ConstCellPtr cell, const char* who) {
    if (cell && cell->type == Cell::Type::error_signal) {
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
    throw logic_error("Cell is not callable");
}

void Cell::clear_descendant_parent_links() {}

MapCell::MapCell() : Cell(Type::map) {}

MapCell::MapCell(unordered_map<string, CellPtr> fields)
    : Cell(Type::map), value(move(fields)) {}

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

FunCell::FunCell() : Cell(Type::function) {
}

FunCell::FunCell(Implementation implementation)
    : Cell(Type::function), value(move(implementation)) {}

CellPtr FunCell::call(const vector<CellPtr>& arguments, CellPtr current_vm) const {
    if (!value) {
        throw logic_error("FunCell has no implementation");
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
