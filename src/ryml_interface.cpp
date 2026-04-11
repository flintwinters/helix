#include <ryml_interface.hpp>

#include <cerrno>
#include <c4/std/string.hpp>
#include <cstdlib>
#include <vector>

using namespace std;

void attach_parent(Cell* parent, Cell* child) {
    if (parent == nullptr || child == nullptr) {
        return;
    }

    child->parents.push_back(parent);
}

void cell_to_yaml_node(const Cell& cell, ryml::NodeRef* node) {
    switch (cell.type()) {
    case Cell::INT:
        *node << cell.as_int();
        return;
    case Cell::STR:
        if (const string* value = cell.str_value()) {
            node->set_val(ryml::csubstr(value->data(), value->size()));
        } else {
            const char* empty = "";
            node->set_val(ryml::csubstr(empty, static_cast<size_t>(0)));
        }
        return;
    case Cell::VEC:
        *node |= ryml::SEQ;
        for (Cell* child : *cell.vec_value()) {
            ryml::NodeRef child_node = node->append_child();
            cell_to_yaml_node(*child, &child_node);
        }
        return;
    case Cell::MAP:
        *node |= ryml::MAP;
        for (const auto& [key, value] : *cell.map_value()) {
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

string cell_to_yaml_string(const Cell& cell) {
    ryml::Tree tree = cell_to_yaml(cell);
    return ryml::emitrs_yaml<std::string>(tree);
}

Cell* yaml_node_to_cell(const ryml::ConstNodeRef& node) {
    if (node.is_map()) {
        Cell* cell = &register_success(make_map_cell());
        auto* entries = cell->map_value();
        for (const ryml::ConstNodeRef& child : node.children()) {
            const string key(child.key().str, child.key().len);
            Cell* child_cell = yaml_node_to_cell(child);
            attach_parent(cell, child_cell);
            (*entries)[key] = child_cell;
        }
        return cell;
    }

    if (node.is_seq()) {
        Cell* cell = &register_success(make_vec_cell());
        auto* values = cell->vec_value();
        for (const ryml::ConstNodeRef& child : node.children()) {
            Cell* child_cell = yaml_node_to_cell(child);
            attach_parent(cell, child_cell);
            values->push_back(child_cell);
        }
        return cell;
    }

    const string value(node.val().str, node.val().len);
    char* end = nullptr;
    errno = 0;
    const long parsed = std::strtol(value.c_str(), &end, 10);
    const bool is_integer = !value.empty() && end == value.c_str() + value.size() && errno == 0;
    if (is_integer) {
        return &register_success(make_int_cell(static_cast<int>(parsed)));
    }

    return &register_success(make_string_cell(value));
}

Cell& parse_yaml_to_cells(const string& yaml, Cell& zygote) {
    const ryml::csubstr yaml_view(yaml.data(), yaml.size());
    ryml::Tree tree = ryml::parse_in_arena(yaml_view);
    Cell* root = yaml_node_to_cell(tree.rootref());
    attach_parent(&zygote, root);
    return *root;
}
