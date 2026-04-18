#pragma once

#include <functional>
#include <string>
#include <unordered_map>
#include <vector>

#include <ryml.hpp>

#include "core.hpp"

using MakeErr = std::function<Ptr(std::string)>;
using MakeInt = std::function<Ptr(int)>;
using MakeStr = std::function<Ptr(std::string)>;
using MakeVec = std::function<Ptr(std::vector<Ptr>)>;
using MakeMap = std::function<Ptr(std::unordered_map<std::string, Ptr>)>;

Ptr yaml_file_to_cell(
    const std::string& filename,
    const MakeErr& make_err,
    const MakeInt& make_int,
    const MakeStr& make_str,
    const MakeVec& make_vec,
    const MakeMap& make_map
);

ryml::Tree cell_to_ryml_tree(const Ptr& cell);
