#pragma once

#include <functional>
#include <string_view>
#include <vector>

#include "runtime.hpp"

namespace helix {

using BuiltinArguments = std::vector<Value>;
using BuiltinImplementation = std::function<Value(const BuiltinArguments&, Program&, VM&)>;

class BuiltinRegistry {
 public:
  void register_builtin(std::string name, BuiltinImplementation implementation);
  [[nodiscard]] bool has_builtin(std::string_view name) const;
  Value call(std::string_view name, const BuiltinArguments& arguments, Program& program, VM& vm) const;

 private:
  std::unordered_map<std::string, BuiltinImplementation> entries_ {};
};

BuiltinRegistry create_default_builtins();

}  // namespace helix
