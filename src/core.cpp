#include <cstdint>
#include <functional>
#include <string_view>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

using namespace std;

class Cell {
 public:
  Cell() = default;
  Cell(const Cell&) = default;
  Cell(Cell&&) = default;
  Cell& operator=(const Cell&) = default;
  Cell& operator=(Cell&&) = default;
  virtual ~Cell() = default;

  [[nodiscard]] virtual string_view cell_type() const noexcept { return "Cell"; }
  [[nodiscard]] virtual bool is_signal() const noexcept { return false; }
};

using CellPtr = shared_ptr<Cell>;
using ConstCellPtr = shared_ptr<const Cell>;
using CellVec = vector<CellPtr>;
using CellMap = unordered_map<string, CellPtr>;

class MapCell final : public Cell {
 public:
  MapCell() = default;
  explicit MapCell(CellMap fields) : fields_(move(fields)) {}

  [[nodiscard]] string_view cell_type() const noexcept override { return "Map"; }
  [[nodiscard]] bool has(const string& key) const { return fields_.contains(key); }
  [[nodiscard]] CellPtr get(const string& key) const { return fields_.at(key); }
  void set(string key, CellPtr value) { fields_[move(key)] = move(value); }
  [[nodiscard]] const CellMap& fields() const noexcept { return fields_; }
  [[nodiscard]] CellMap& fields() noexcept { return fields_; }

 private:
  CellMap fields_ {};
};

class VecCell final : public Cell {
 public:
  VecCell() = default;
  explicit VecCell(CellVec elements) : elements_(move(elements)) {}

  [[nodiscard]] string_view cell_type() const noexcept override { return "Vec"; }
  void append(CellPtr value) { elements_.push_back(move(value)); }
  [[nodiscard]] CellPtr at(size_t index) const { return elements_.at(index); }
  [[nodiscard]] size_t size() const noexcept { return elements_.size(); }
  [[nodiscard]] const CellVec& elements() const noexcept { return elements_; }
  [[nodiscard]] CellVec& elements() noexcept { return elements_; }

 private:
  CellVec elements_ {};
};

class IntCell final : public Cell {
 public:
  explicit IntCell(int64_t value) : value_(value) {}

  [[nodiscard]] string_view cell_type() const noexcept override { return "Int"; }
  [[nodiscard]] int64_t value() const noexcept { return value_; }
  void set_value(int64_t value) noexcept { value_ = value; }

 private:
  int64_t value_ {};
};

class StrCell final : public Cell {
 public:
  StrCell() = default;
  explicit StrCell(string value) : value_(move(value)) {}

  [[nodiscard]] string_view cell_type() const noexcept override { return "Str"; }
  [[nodiscard]] const string& value() const noexcept { return value_; }
  void set_value(string value) { value_ = move(value); }

 private:
  string value_ {};
};

class FunCell final : public Cell {
 public:
  using Implementation = function<CellPtr(const CellVec&, CellPtr)>;

  FunCell() = default;
  explicit FunCell(Implementation implementation) : implementation_(move(implementation)) {}

  [[nodiscard]] string_view cell_type() const noexcept override { return "Fun"; }
  [[nodiscard]] bool callable() const noexcept { return static_cast<bool>(implementation_); }
  CellPtr call(const CellVec& arguments, CellPtr current_vm) const {
    return implementation_(arguments, move(current_vm));
  }

 private:
  Implementation implementation_ {};
};
