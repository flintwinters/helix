#pragma once

#include <filesystem>

#include <core.hpp>

using namespace std;

void expand_includes_in_root_map(const shared_ptr<MapCell>& root_cell, const filesystem::path& source_path);
