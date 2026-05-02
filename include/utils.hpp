#pragma once

#include <string>
#include <string_view>

#include "runtime.hpp"

namespace helix {

[[nodiscard]] std::string to_string(const Value& value);
[[nodiscard]] std::string to_string(const Signal& signal);
[[nodiscard]] std::string_view to_string(VM::Status status);

}  // namespace helix
