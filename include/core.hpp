#pragma once

#include <string_view>

#include "runtime.hpp"

namespace helix {

class Evaluator {
 public:
  explicit Evaluator(VM& vm);

  Value evaluate(const Value& expression, Program& program);
  Value evaluate_node(std::string_view node_name, Program& program);

 private:
  VM& vm_;
};

}  // namespace helix
