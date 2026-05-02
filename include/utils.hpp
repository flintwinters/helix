#pragma once

#include <string>
#include <string_view>

#include "runtime.hpp"

using namespace std;

[[nodiscard]] string to_string(const Value& value);
[[nodiscard]] string to_string(const Signal& signal);
[[nodiscard]] string_view to_string(VM::Status status);
