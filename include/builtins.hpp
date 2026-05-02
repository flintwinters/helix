#pragma once

#include <functional>
#include <string_view>
#include <vector>

#include <runtime.hpp>

using namespace std;

using BuiltinArguments = vector<Value>;
using BuiltinImplementation = function<Value(const BuiltinArguments&, Program&, VM&)>;

class BuiltinRegistry {
 public:
  void register_builtin(string name, BuiltinImplementation implementation);
  [[nodiscard]] bool has_builtin(string_view name) const;
  Value call(string_view name, const BuiltinArguments& arguments, Program& program, VM& vm) const;

 private:
  unordered_map<string, BuiltinImplementation> entries_ {};
};

BuiltinRegistry create_default_builtins();
