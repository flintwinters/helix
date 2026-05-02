#include <cstdint>
#include <functional>
#include <memory>
#include <stdexcept>
#include <string_view>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

using namespace std;

class Cell;
using CellPtr = shared_ptr<Cell>;
using ConstCellPtr = shared_ptr<const Cell>;
using CellVec = vector<CellPtr>;
using CellMap = unordered_map<string, CellPtr>;

class Cell {
 public:
  Cell() = default;
  Cell(const Cell&) = default;
  Cell(Cell&&) = default;
  Cell& operator=(const Cell&) = default;
  Cell& operator=(Cell&&) = default;
  virtual ~Cell() = default;

  virtual string_view cell_type() const noexcept { return "Cell"; }
  virtual bool is_signal() const noexcept { return false; }
  virtual bool callable() const noexcept { return false; }
  virtual size_t size() const noexcept { return 0; }
  virtual CellPtr call(const CellVec& arguments, CellPtr current_vm) const {
    throw logic_error("Cell is not callable");
  }
};

class MapCell final : public Cell {
 public:
  MapCell() = default;
  explicit MapCell(CellMap fields) : value(move(fields)) {}

  string_view cell_type() const noexcept override { return "Map"; }
  size_t size() const noexcept override { return value.size(); }

  CellMap value {};
};

class VecCell final : public Cell {
 public:
  VecCell() = default;
  explicit VecCell(CellVec elements) : value(move(elements)) {}

  string_view cell_type() const noexcept override { return "Vec"; }
  size_t size() const noexcept override { return value.size(); }

  CellVec value {};
};

class IntCell final : public Cell {
 public:
  explicit IntCell(int64_t initial_value) : value(initial_value) {}

  string_view cell_type() const noexcept override { return "Int"; }

  int64_t value {};
};

class StrCell final : public Cell {
 public:
  StrCell() = default;
  explicit StrCell(string initial_value) : value(move(initial_value)) {}

  string_view cell_type() const noexcept override { return "Str"; }
  size_t size() const noexcept override { return value.size(); }

  string value {};
};

class FunCell final : public Cell {
 public:
  using Implementation = function<CellPtr(const CellVec&, CellPtr)>;

  FunCell() = default;
  explicit FunCell(Implementation implementation) : value(move(implementation)) {}

  string_view cell_type() const noexcept override { return "Fun"; }
  bool callable() const noexcept override { return static_cast<bool>(value); }
  CellPtr call(const CellVec& arguments, CellPtr current_vm) const override {
    if (!value) {
      throw logic_error("FunCell has no implementation");
    }
    return value(arguments, move(current_vm));
  }

  Implementation value {};
};
