#pragma once

#include <memory>
#include <string_view>

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
