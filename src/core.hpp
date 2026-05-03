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

struct Cell {
    enum class Type {
        base,
        map,
        vec,
        integer,
        string,
        function,
        signal,
        return_signal,
        error_signal,
    };

    bool callable = false;

    Cell();
    explicit Cell(Type initial_type);
    Cell(const Cell&);
    Cell(Cell&&);
    Cell& operator=(const Cell&);
    Cell& operator=(Cell&&);
    virtual ~Cell();

    Type type {Type::base};
    CellPtr parent {};
    virtual bool is_signal() const noexcept;
    virtual size_t size() const noexcept;
    virtual CellPtr call(const vector<CellPtr>& arguments, CellPtr current_vm) const;
};

struct MapCell final : public Cell {
    MapCell();
    explicit MapCell(unordered_map<string, CellPtr> fields);

    size_t size() const noexcept override;

    unordered_map<string, CellPtr> value {};
};

struct VecCell final : public Cell {
    VecCell();
    explicit VecCell(vector<CellPtr> elements);

    size_t size() const noexcept override;

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

    bool is_signal() const noexcept override;

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
