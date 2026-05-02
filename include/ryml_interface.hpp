#pragma once

#include <string>
#include <string_view>

#include "runtime.hpp"

namespace helix {

class YamlInterface {
 public:
  Program load_program(std::string_view source_text) const;
  Program load_program_file(std::string_view path) const;
  std::string dump_program(const Program& program) const;
  std::string dump_vm(const VM& vm) const;
};

}  // namespace helix
