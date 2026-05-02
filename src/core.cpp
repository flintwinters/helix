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
  enum class Type {
    base,
    map,
    vec,
    integer,
    string,
    function,
  };

  Cell() = default;
  explicit Cell(Type initial_type) : type(initial_type) {}
  Cell(const Cell&) = default;
  Cell(Cell&&) = default;
  Cell& operator=(const Cell&) = default;
  Cell& operator=(Cell&&) = default;
  virtual ~Cell() = default;

  Type type {Type::base};
  virtual bool is_signal() const noexcept { return false; }
  virtual bool callable() const noexcept { return false; }
  virtual size_t size() const noexcept { return 0; }
  virtual CellPtr call(const CellVec& arguments, CellPtr current_vm) const {
    throw logic_error("Cell is not callable");
  }
};

class MapCell final : public Cell {
 public:
  MapCell() : Cell(Type::map) {}
  explicit MapCell(CellMap fields) : Cell(Type::map), value(move(fields)) {}

  size_t size() const noexcept override { return value.size(); }

  CellMap value {};
};

class VecCell final : public Cell {
 public:
  VecCell() : Cell(Type::vec) {}
  explicit VecCell(CellVec elements) : Cell(Type::vec), value(move(elements)) {}

  size_t size() const noexcept override { return value.size(); }

  CellVec value {};
};

class IntCell final : public Cell {
 public:
  explicit IntCell(int64_t initial_value) : Cell(Type::integer), value(initial_value) {}

  int64_t value {};
};

class StrCell final : public Cell {
 public:
  StrCell() : Cell(Type::string) {}
  explicit StrCell(string initial_value) : Cell(Type::string), value(move(initial_value)) {}

  size_t size() const noexcept override { return value.size(); }

  string value {};
};

class FunCell final : public Cell {
 public:
  using Implementation = function<CellPtr(const CellVec&, CellPtr)>;

  FunCell() : Cell(Type::function) {}
  explicit FunCell(Implementation implementation) : Cell(Type::function), value(move(implementation)) {}

  bool callable() const noexcept override { return static_cast<bool>(value); }
  CellPtr call(const CellVec& arguments, CellPtr current_vm) const override {
    if (!value) {
      throw logic_error("FunCell has no implementation");
    }
    return value(arguments, move(current_vm));
  }

  Implementation value {};
};
