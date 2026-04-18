#include "ryml_interface.hpp"

#include <c4/std/string.hpp>
#include <ryml.hpp>
#include <algorithm>
#include <cctype>
#include <fstream>

using namespace std;

static string to_string_copy(c4::csubstr s) {
    return string(s.str, s.len);
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
