#pragma once

#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

using namespace std;

class Cell;
using CellPtr = shared_ptr<Cell>;
using ConstCellPtr = shared_ptr<const Cell>;
struct MapCell;

struct Cell {
    enum class Type {
        base,
        map,
        vec,
        integer,
        string,
        nil,
        function,
        signal,
        return_signal,
        error_signal,
    };

    Cell();
    explicit Cell(Type initial_type);
    Cell(const Cell&);
    Cell(Cell&&);
    Cell& operator=(const Cell&);
    Cell& operator=(Cell&&);
    virtual ~Cell();

    Type type {Type::base};
    CellPtr parent {};
    virtual size_t size() const noexcept;
    virtual CellPtr call(const vector<CellPtr>& arguments, CellPtr current_vm) const;
    virtual void clear_descendant_parent_links();
};

struct MapCell final : public Cell, public enable_shared_from_this<MapCell> {
    MapCell();
    explicit MapCell(unordered_map<string, CellPtr> fields);

    void set(const string& key, CellPtr child);
    size_t size() const noexcept override;
    void clear_descendant_parent_links() override;

    unordered_map<string, CellPtr> value {};
};

struct VecCell final : public Cell {
    VecCell();
    explicit VecCell(vector<CellPtr> elements);

    size_t size() const noexcept override;
    void clear_descendant_parent_links() override;

    vector<CellPtr> value {};
};

struct IntCell final : public Cell {
    explicit IntCell(int64_t initial_value);

    int64_t value {};
};

struct StrCell final : public Cell {
    StrCell();
    explicit StrCell(string initial_value);

    size_t size() const noexcept override;

    string value {};
};

struct NilCell final : public Cell {
    NilCell();
};

struct FunCell final : public Cell {
    using Implementation = function<CellPtr(const vector<CellPtr>&, CellPtr)>;

    FunCell();
    explicit FunCell(Implementation implementation);

    CellPtr call(const vector<CellPtr>& arguments, CellPtr current_vm) const override;

    Implementation value {};
};

struct SigCell : public Cell {
    SigCell();
    explicit SigCell(CellPtr initial_value);
    SigCell(Type initial_type, CellPtr initial_value);

    void clear_descendant_parent_links() override;

    CellPtr value {};
};

struct RetCell final : public SigCell {
    RetCell();
    explicit RetCell(CellPtr initial_value);
};

struct ErrCell final : public SigCell {
    ErrCell();
    ErrCell(string initial_message, CellPtr initial_value);

    string message {};
};

enum class VmStatus {
    ready,
    running,
    finished,
    error,
    signaled,
};

using EvalCellFn = CellPtr(*)(CellPtr, const shared_ptr<MapCell>&);
using ResolveCellFn = CellPtr(*)(CellPtr, const shared_ptr<MapCell>&);
using AdvanceVmFn = CellPtr(*)(const shared_ptr<MapCell>&);
using RenderShowFn = string(*)(ConstCellPtr);
using MakeErrorFn = CellPtr(*)(const string&, CellPtr);

CellPtr make_error_cell(const string& message, CellPtr value = nullptr);
bool is_signal_cell(ConstCellPtr cell);
CellPtr map_field_cell(ConstCellPtr map_cell, const string& key);
shared_ptr<MapCell> map_field_map(ConstCellPtr map_cell, const string& key);
shared_ptr<VecCell> map_field_vec(ConstCellPtr map_cell, const string& key);
const StrCell* map_field_string(ConstCellPtr map_cell, const string& key);
const IntCell* map_field_int(ConstCellPtr map_cell, const string& key);
shared_ptr<MapCell> expect_map_cell(CellPtr cell);
const char* vm_status_name(VmStatus status);
shared_ptr<MapCell> ensure_vm_state(const shared_ptr<MapCell>& vm);
CellPtr vm_status_cell(const shared_ptr<MapCell>& vm);
VmStatus vm_status(const shared_ptr<MapCell>& vm);
shared_ptr<VecCell> vm_frames(const shared_ptr<MapCell>& vm);
void set_vm_status(const shared_ptr<MapCell>& vm, VmStatus status);
void clear_vm_terminal_fields(const shared_ptr<MapCell>& vm);
bool vm_is_terminal(const shared_ptr<MapCell>& vm);
CellPtr vm_result(const shared_ptr<MapCell>& vm);
void arm_list_frame(const shared_ptr<MapCell>& vm, CellPtr sequence_cell, int64_t start_index = 0);
shared_ptr<MapCell> make_finished_state_cell();
void attach_finished_state(const shared_ptr<MapCell>& root_cell, CellPtr result);
void attach_terminal_state(const shared_ptr<MapCell>& root_cell, CellPtr result);
CellPtr expect_form_arity(size_t actual_arity, size_t expected_arity, const char* who);
CellPtr expect_int_cell(ConstCellPtr cell, const char* who);

void initialize_builtins(
    EvalCellFn evaluate_cell_fn,
    ResolveCellFn resolve_cell_fn,
    AdvanceVmFn advance_vm_fn,
    RenderShowFn render_show_fn,
    MakeErrorFn make_error_fn);

shared_ptr<MapCell> make_zygote();
