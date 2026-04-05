#include "ryml_interface.hpp"

#include <cerrno>
#include <cstdlib>

using namespace std;

void cell_to_yaml_node(const Cell& cell, ryml::NodeRef* node) {
    switch (cell.t) {
    case Cell::INT:
        *node << cell.i;
        return;
    case Cell::STR:
        if (cell.s) {
            node->set_val(ryml::csubstr(cell.s->data(), cell.s->size()));
        } else {
            const char* empty = "";
            node->set_val(ryml::csubstr(empty, static_cast<size_t>(0)));
        }
        return;
    case Cell::VEC:
        *node |= ryml::SEQ;
        for (Cell* child : *cell.v) {
            ryml::NodeRef child_node = node->append_child();
            cell_to_yaml_node(*child, &child_node);
        }
        return;
    case Cell::MAP:
        *node |= ryml::MAP;
        for (const auto& [key, value] : *cell.m) {
            ryml::NodeRef child_node = node->append_child();
            child_node.set_key(ryml::csubstr(key.data(), key.size()));
            cell_to_yaml_node(*value, &child_node);
        }
        return;
    case Cell::FUN:
        node->set_val("FUN");
        return;
    case Cell::ANY:
        node->set_val("ANY");
        return;
    }
}

ryml::Tree cell_to_yaml(const Cell& cell) {
    ryml::Tree tree;
    ryml::NodeRef root = tree.rootref();
    cell_to_yaml_node(cell, &root);
    return tree;
}

Cell* yaml_node_to_cell(const ryml::ConstNodeRef& node) {
    if (node.is_map()) {
        Cell* cell = new Cell();
        cell->t = Cell::MAP;
        cell->m = new unordered_map<string, Cell*>();
        for (const ryml::ConstNodeRef& child : node.children()) {
            const string key(child.key().str, child.key().len);
            (*cell->m)[key] = yaml_node_to_cell(child);
        }
        return cell;
    }

    if (node.is_seq()) {
        Cell* cell = new Cell();
        cell->t = Cell::VEC;
        cell->v = new vector<Cell*>();
        for (const ryml::ConstNodeRef& child : node.children()) {
            cell->v->push_back(yaml_node_to_cell(child));
        }
        return cell;
    }

    const string value(node.val().str, node.val().len);
    char* end = nullptr;
    errno = 0;
    const long parsed = std::strtol(value.c_str(), &end, 10);
    const bool is_integer = !value.empty() && end == value.c_str() + value.size() && errno == 0;
    if (is_integer) {
        return new Cell(static_cast<int>(parsed));
    }

    return new Cell(value.c_str());
}

Cell& parse_yaml_to_cells(const string& yaml) {
    const ryml::csubstr yaml_view(yaml.data(), yaml.size());
    ryml::Tree tree = ryml::parse_in_arena(yaml_view);
    return *yaml_node_to_cell(tree.rootref());
}
