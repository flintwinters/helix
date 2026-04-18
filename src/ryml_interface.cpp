#include "ryml_interface.hpp"

#include <c4/std/string.hpp>
#include <ryml.hpp>
#include <algorithm>
#include <cctype>
#include <fstream>
#include <sstream>
#include <unordered_set>

using namespace std;

static string to_string_copy(c4::csubstr s) {
    return string(s.str, s.len);
}

static string yaml_quote(const string& text) {
    ostringstream out;
    out << '"';
    for (char ch : text) {
        switch (ch) {
            case '\\':
                out << "\\\\";
                break;
            case '"':
                out << "\\\"";
                break;
            case '\n':
                out << "\\n";
                break;
            case '\r':
                out << "\\r";
                break;
            case '\t':
                out << "\\t";
                break;
            default:
                out << ch;
                break;
        }
    }
    out << '"';
    return out.str();
}

static string indent(size_t depth) {
    return string(depth, ' ');
}

static string cell_to_yaml_text(
    const Ptr& cell,
    size_t depth,
    unordered_set<const Cell*>& seen
);

static string yaml_inline_value(
    const Ptr& cell,
    size_t depth,
    unordered_set<const Cell*>& seen
) {
    if (!cell) {
        return "null";
    }

    if (shared_ptr<Int> int_cell = dynamic_pointer_cast<Int>(cell)) {
        return to_string(int_cell->v);
    }

    if (shared_ptr<Str> str_cell = dynamic_pointer_cast<Str>(cell)) {
        return yaml_quote(str_cell->v);
    }

    if (shared_ptr<Err> err_cell = dynamic_pointer_cast<Err>(cell)) {
        return yaml_quote(err_cell->msg);
    }

    if (dynamic_pointer_cast<Fun>(cell)) {
        return yaml_quote(cell->str());
    }

    if (dynamic_pointer_cast<Vec>(cell) || dynamic_pointer_cast<Map>(cell)) {
        return "\n" + cell_to_yaml_text(cell, depth, seen);
    }

    return yaml_quote(cell->str());
}

static string cell_to_yaml_text(
    const Ptr& cell,
    size_t depth,
    unordered_set<const Cell*>& seen
) {
    if (!cell) {
        return indent(depth) + "null\n";
    }

    if (shared_ptr<Int> int_cell = dynamic_pointer_cast<Int>(cell)) {
        return indent(depth) + to_string(int_cell->v) + "\n";
    }

    if (shared_ptr<Str> str_cell = dynamic_pointer_cast<Str>(cell)) {
        return indent(depth) + yaml_quote(str_cell->v) + "\n";
    }

    if (shared_ptr<Err> err_cell = dynamic_pointer_cast<Err>(cell)) {
        return indent(depth) + yaml_quote(err_cell->msg) + "\n";
    }

    if (dynamic_pointer_cast<Fun>(cell)) {
        return indent(depth) + yaml_quote(cell->str()) + "\n";
    }

    if (shared_ptr<Vec> vec_cell = dynamic_pointer_cast<Vec>(cell)) {
        if (!seen.insert(cell.get()).second) {
            return indent(depth) + yaml_quote("<recursive>") + "\n";
        }

        if (vec_cell->v.empty()) {
            return indent(depth) + "[]\n";
        }

        ostringstream out;
        for (const Ptr& item : vec_cell->v) {
            out << indent(depth) << "-";
            const string item_text = yaml_inline_value(item, depth + 2, seen);
            if (!item_text.empty() && item_text[0] != '\n') {
                out << " " << item_text << "\n";
            } else {
                out << "\n" << item_text.substr(1);
            }
        }
        seen.erase(cell.get());
        return out.str();
    }

    if (shared_ptr<Map> map_cell = dynamic_pointer_cast<Map>(cell)) {
        if (!seen.insert(cell.get()).second) {
            return indent(depth) + yaml_quote("<recursive>") + "\n";
        }

        if (map_cell->m.empty()) {
            return indent(depth) + "{}\n";
        }

        ostringstream out;
        for (const auto& [key, value] : map_cell->m) {
            out << indent(depth) << yaml_quote(key) << ":";
            const string value_text = yaml_inline_value(value, depth + 2, seen);
            if (!value_text.empty() && value_text[0] != '\n') {
                out << " " << value_text << "\n";
            } else {
                out << "\n" << value_text.substr(1);
            }
        }
        seen.erase(cell.get());
        return out.str();
    }

    return indent(depth) + yaml_quote(cell->str()) + "\n";
}

ryml::Tree cell_to_ryml_tree(const Ptr& cell) {
    unordered_set<const Cell*> seen;
    const string yaml_text = cell_to_yaml_text(cell, 0, seen);
    return ryml::parse_in_arena(ryml::to_csubstr(yaml_text));
}

struct YamlParser {
    const MakeErr& make_err;
    const MakeInt& make_int;
    const MakeStr& make_str;
    const MakeVec& make_vec;
    const MakeMap& make_map;

    Ptr scalar_to_cell(c4::csubstr scalar) const {
        const string text = to_string_copy(scalar);

        if (!text.empty()) {
            size_t start = 0;
            if (text[0] == '-' || text[0] == '+') {
                start = 1;
            }

            const bool is_integer =
                start < text.size() &&
                all_of(text.begin() + static_cast<ptrdiff_t>(start), text.end(), [](unsigned char ch) {
                    return isdigit(ch) != 0;
                });

            if (is_integer) {
                return make_int(stoi(text));
            }
        }

        return make_str(text);
    }

    Ptr document_to_cell(ryml::ConstNodeRef node) const {
        if (!node.has_children()) {
            return make_map({});
        }
        return node_to_cell(node[0]);
    }

    Ptr map_to_cell(ryml::ConstNodeRef node) const {
        unordered_map<string, Ptr> values;
        values.reserve(node.num_children());
        for (const ryml::ConstNodeRef child : node.children()) {
            values.emplace(to_string_copy(child.key()), node_to_cell(child));
        }
        return make_map(move(values));
    }

    Ptr sequence_to_cell(ryml::ConstNodeRef node) const {
        vector<Ptr> values;
        values.reserve(node.num_children());
        for (const ryml::ConstNodeRef child : node.children()) {
            values.push_back(node_to_cell(child));
        }
        return make_vec(move(values));
    }

    Ptr node_to_cell(ryml::ConstNodeRef node) const {
        if (node.invalid()) {
            return make_err("invalid yaml node");
        }

        if (node.is_stream() || node.is_doc()) {
            return document_to_cell(node);
        }

        if (node.is_map()) {
            return map_to_cell(node);
        }

        if (node.is_seq()) {
            return sequence_to_cell(node);
        }

        if (node.has_val()) {
            return scalar_to_cell(node.val());
        }

        return make_map({});
    }
};

Ptr yaml_file_to_cell(
    const string& filename,
    const MakeErr& make_err,
    const MakeInt& make_int,
    const MakeStr& make_str,
    const MakeVec& make_vec,
    const MakeMap& make_map
) {
    ifstream input(filename);
    if (!input) {
        return make_err("unable to open file: " + filename);
    }

    const string yaml((istreambuf_iterator<char>(input)), istreambuf_iterator<char>());
    const YamlParser parser{make_err, make_int, make_str, make_vec, make_map};
    ryml::Tree tree = ryml::parse_in_arena(ryml::to_csubstr(yaml));
    return parser.node_to_cell(tree.rootref());
}
