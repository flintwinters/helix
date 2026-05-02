#pragma once

#include <string_view>

#include <runtime.hpp>

using namespace std;

class Evaluator {
 public:
  explicit Evaluator(VM& vm);

  Value evaluate(const Value& expression, Program& program);
  Value evaluate_node(string_view node_name, Program& program);

 private:
  VM& vm_;
};
