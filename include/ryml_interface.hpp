#pragma once

#include <string>

#include <ryml.hpp>

#include <core.hpp>

void cell_to_yaml_node(const Cell& cell, ryml::NodeRef* node);
ryml::Tree cell_to_yaml(const Cell& cell);
std::string cell_to_yaml_string(const Cell& cell);
Cell* yaml_node_to_cell(const ryml::ConstNodeRef& node);
Cell& parse_yaml_to_cells(const std::string& yaml, Cell& zygote);
