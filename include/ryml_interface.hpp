#pragma once

#include <string>
#include <string_view>

#include <runtime.hpp>

using namespace std;

class YamlInterface {
 public:
  Program load_program(string_view source_text) const;
  Program load_program_file(string_view path) const;
  string dump_program(const Program& program) const;
  string dump_vm(const VM& vm) const;
};
