#pragma once

#include <cstddef>
#include <string>
#include <vector>

#include "core.hpp"

std::string pad(std::size_t depth);
Ptr error(std::string s);
Ptr current_args(const Ptr& vm);
Ptr eval_with_args(const Ptr& vm, const Ptr& actor, std::vector<Ptr> args);
