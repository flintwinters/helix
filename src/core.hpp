#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

using namespace std;

class Cell;
using CellPtr = shared_ptr<Cell>;
using ConstCellPtr = shared_ptr<const Cell>;
struct MapCell;
struct ScopeCell;
struct VmCell;

struct Cell {
    enum class Type {
        base,
        map,
        scope,
        vm,
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
    const char* class_name {"Cell"};
    CellPtr parent {};
    virtual size_t size() const noexcept;
    virtual CellPtr call(const vector<CellPtr>& arguments, CellPtr current_vm) const;
    virtual CellPtr lookup(const string& name, const shared_ptr<VmCell>& root_cell) const;
    virtual CellPtr lookup_member(const string& name, const shared_ptr<VmCell>& root_cell) const;
    virtual void clear_descendant_parent_links();
};

struct MapCell : public Cell, public enable_shared_from_this<MapCell> {
    MapCell();
    explicit MapCell(unordered_map<string, CellPtr> fields);

    void set(const string& key, CellPtr child);
    size_t size() const noexcept override;
    virtual CellPtr lookup_member(const string& name, const shared_ptr<VmCell>& root_cell) const override;
    void clear_descendant_parent_links() override;

    unordered_map<string, CellPtr> value {};
};

struct ScopeCell final : public MapCell {
    ScopeCell();
    explicit ScopeCell(unordered_map<string, CellPtr> fields);

    CellPtr lookup(const string& name, const shared_ptr<VmCell>& root_cell) const override;
};

struct VmCell final : public MapCell {
    VmCell();
    explicit VmCell(unordered_map<string, CellPtr> fields);

    CellPtr lookup(const string& name, const shared_ptr<VmCell>& root_cell) const override;
};

struct VecCell final : public Cell, public enable_shared_from_this<VecCell> {
    VecCell();
    explicit VecCell(vector<CellPtr> elements);

    void append(CellPtr child);
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
    using Implementation = CellPtr (*)(const vector<CellPtr>&, CellPtr);

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

using EvalCellFn = CellPtr(*)(CellPtr, const shared_ptr<VmCell>&);
using ResolveCellFn = CellPtr(*)(CellPtr, const shared_ptr<VmCell>&);
using AdvanceVmFn = CellPtr(*)(const shared_ptr<VmCell>&);
using RenderShowFn = string(*)(ConstCellPtr);
using MakeErrorFn = CellPtr(*)(const string&, CellPtr);

namespace CellField {
inline constexpr const char* actor_type = "actor_type";
inline constexpr const char* body = "body";
inline constexpr const char* breakpoints = "breakpoints";
inline constexpr const char* context_type = "context_type";
inline constexpr const char* error = "error";
inline constexpr const char* failed_segment = "failed_segment";
inline constexpr const char* frames = "frames";
inline constexpr const char* include = "include";
inline constexpr const char* internal_body = "__body";
inline constexpr const char* kind = "kind";
inline constexpr const char* main = "main";
inline constexpr const char* message = "message";
inline constexpr const char* name = "name";
inline constexpr const char* params = "params";
inline constexpr const char* pc = "pc";
inline constexpr const char* receiver_type = "receiver_type";
inline constexpr const char* resolved_prefix = "resolved_prefix";
inline constexpr const char* result = "result";
inline constexpr const char* segment = "segment";
inline constexpr const char* segment_index = "segment_index";
inline constexpr const char* signal_type = "signal_type";
inline constexpr const char* source = "source";
inline constexpr const char* state = "state";
inline constexpr const char* status = "status";
inline constexpr const char* value = "value";
inline constexpr const char* yield_reason = "yield_reason";
}

namespace CellValue {
inline constexpr const char* breakpoint = "breakpoint";
inline constexpr const char* breakpoint_error = "breakpoint_error";
inline constexpr const char* error = "error";
inline constexpr const char* finished = "finished";
inline constexpr const char* function = "function";
inline constexpr const char* invalid_actor = "invalid_actor";
inline constexpr const char* lookup_error = "lookup_error";
inline constexpr const char* path_error = "path_error";
inline constexpr const char* ready = "ready";
inline constexpr const char* resolution_error = "resolution_error";
inline constexpr const char* return_signal = "return";
inline constexpr const char* running = "running";
inline constexpr const char* signal = "signal";
inline constexpr const char* signaled = "signaled";
inline constexpr const char* unresolved_actor = "unresolved_actor";
inline constexpr const char* unresolved_name = "unresolved_name";
}

CellPtr make_error_cell(const string& message, CellPtr value = nullptr);
bool is_signal_cell(ConstCellPtr cell);
const char* cell_class_name(ConstCellPtr cell);
const char* cell_class_name(const Cell* cell);
bool is_map_like_cell(ConstCellPtr cell);
CellPtr map_field_cell(ConstCellPtr map_cell, const string& key);
CellPtr cell_at_path(CellPtr root_cell, ConstCellPtr path_cell);
shared_ptr<VecCell> cell_path_from_root(const shared_ptr<VmCell>& root_cell, ConstCellPtr target_cell);
bool cell_paths_equal(ConstCellPtr left_path, ConstCellPtr right_path);
shared_ptr<MapCell> map_field_map(ConstCellPtr map_cell, const string& key);
shared_ptr<ScopeCell> map_field_scope(ConstCellPtr map_cell, const string& key);
shared_ptr<VecCell> map_field_vec(ConstCellPtr map_cell, const string& key);
const StrCell* map_field_string(ConstCellPtr map_cell, const string& key);
const IntCell* map_field_int(ConstCellPtr map_cell, const string& key);
shared_ptr<MapCell> expect_map_cell(CellPtr cell);
shared_ptr<ScopeCell> expect_scope_cell(CellPtr cell);
shared_ptr<VmCell> expect_vm_cell(CellPtr cell);
const char* vm_status_name(VmStatus status);
shared_ptr<MapCell> ensure_vm_state(const shared_ptr<VmCell>& vm);
CellPtr vm_status_cell(const shared_ptr<VmCell>& vm);
VmStatus vm_status(const shared_ptr<VmCell>& vm);
shared_ptr<VecCell> vm_frames(const shared_ptr<VmCell>& vm);
shared_ptr<VecCell> vm_breakpoints(const shared_ptr<VmCell>& vm);
void set_vm_status(const shared_ptr<VmCell>& vm, VmStatus status);
void clear_vm_terminal_fields(const shared_ptr<VmCell>& vm);
bool vm_is_terminal(const shared_ptr<VmCell>& vm);
CellPtr vm_result(const shared_ptr<VmCell>& vm);
bool arm_list_frame(const shared_ptr<VmCell>& vm, CellPtr sequence_cell, int64_t start_index = 0);
shared_ptr<MapCell> make_finished_state_cell();
void attach_finished_state(const shared_ptr<VmCell>& root_cell, CellPtr result);
void attach_terminal_state(const shared_ptr<VmCell>& root_cell, CellPtr result);
CellPtr expect_form_arity(size_t actual_arity, size_t expected_arity, const char* who);
CellPtr expect_int_cell(ConstCellPtr cell, const char* who);

void initialize_builtins(
    EvalCellFn evaluate_cell_fn,
    ResolveCellFn resolve_cell_fn,
    AdvanceVmFn advance_vm_fn,
    RenderShowFn render_show_fn,
    MakeErrorFn make_error_fn);

shared_ptr<ScopeCell> make_zygote();
