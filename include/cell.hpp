#pragma once

#include <memory>

using namespace std;

class Cell {
 public:
  enum class Kind {
    cell,
  };

  explicit Cell(Kind cell_kind = Kind::cell) : kind_(cell_kind) {}
  virtual ~Cell() = default;

  [[nodiscard]] Kind kind() const { return kind_; }

 private:
  Kind kind_;
};

using CellPtr = shared_ptr<Cell>;
