#pragma once

#include <c4/yml/node.hpp>
#include <c4/yml/tree.hpp>

#include <core.hpp>

using namespace std;

CellPtr load_root_cell_from_yaml_file(const char* path);
CellPtr cell_from_ryml_node(c4::yml::ConstNodeRef node);
void write_cell_to_ryml_node(ConstCellPtr cell, c4::yml::NodeRef node);
c4::yml::Tree ryml_tree_from_cell(ConstCellPtr root_cell);
string emit_yaml_from_cell(ConstCellPtr root_cell);
