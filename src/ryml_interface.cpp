#include <algorithm>
#include <cctype>
#include <fstream>
#include <functional>
#include <memory>
#include <string>
#include <unordered_map>
#include <vector>

#include <c4/std/string.hpp>
#include <ryml.hpp>

using namespace std;

struct Cell;

using Ptr = shared_ptr<Cell>;
using MakeErr = function<Ptr(string)>;
using MakeInt = function<Ptr(int)>;
using MakeStr = function<Ptr(string)>;
using MakeVec = function<Ptr(vector<Ptr>)>;
using MakeMap = function<Ptr(unordered_map<string, Ptr>)>;

static string to_string_copy(c4::csubstr s) {
    return string(s.str, s.len);
}

static Ptr scalar_to_cell(c4::csubstr scalar, const MakeInt& make_int, const MakeStr& make_str) {
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

static Ptr yaml_node_to_cell(
    ryml::ConstNodeRef node,
    const MakeErr& make_err,
    const MakeInt& make_int,
    const MakeStr& make_str,
    const MakeVec& make_vec,
    const MakeMap& make_map
);

static Ptr yaml_document_to_cell(
    ryml::ConstNodeRef node,
    const MakeErr& make_err,
    const MakeInt& make_int,
    const MakeStr& make_str,
    const MakeVec& make_vec,
    const MakeMap& make_map
) {
    if (!node.has_children()) {
        return make_map({});
    }
    return yaml_node_to_cell(node[0], make_err, make_int, make_str, make_vec, make_map);
}

static Ptr yaml_map_to_cell(
    ryml::ConstNodeRef node,
    const MakeErr& make_err,
    const MakeInt& make_int,
    const MakeStr& make_str,
    const MakeVec& make_vec,
    const MakeMap& make_map
) {
    unordered_map<string, Ptr> values;
    values.reserve(node.num_children());
    for (const auto child : node.children()) {
        values.emplace(
            to_string_copy(child.key()),
            yaml_node_to_cell(child, make_err, make_int, make_str, make_vec, make_map)
        );
    }
    return make_map(move(values));
}

static Ptr yaml_sequence_to_cell(
    ryml::ConstNodeRef node,
    const MakeErr& make_err,
    const MakeInt& make_int,
    const MakeStr& make_str,
    const MakeVec& make_vec,
    const MakeMap& make_map
) {
    vector<Ptr> values;
    values.reserve(node.num_children());
    for (const auto child : node.children()) {
        values.push_back(yaml_node_to_cell(child, make_err, make_int, make_str, make_vec, make_map));
    }
    return make_vec(move(values));
}

static Ptr yaml_node_to_cell(
    ryml::ConstNodeRef node,
    const MakeErr& make_err,
    const MakeInt& make_int,
    const MakeStr& make_str,
    const MakeVec& make_vec,
    const MakeMap& make_map
) {
    if (!node.valid()) {
        return make_err("invalid yaml node");
    }

    if (node.is_stream() || node.is_doc()) {
        return yaml_document_to_cell(node, make_err, make_int, make_str, make_vec, make_map);
    }

    if (node.is_map()) {
        return yaml_map_to_cell(node, make_err, make_int, make_str, make_vec, make_map);
    }

    if (node.is_seq()) {
        return yaml_sequence_to_cell(node, make_err, make_int, make_str, make_vec, make_map);
    }

    if (node.has_val()) {
        return scalar_to_cell(node.val(), make_int, make_str);
    }

    return make_map({});
}

static Ptr yaml_string_to_cell(
    const string& yaml,
    const MakeErr& make_err,
    const MakeInt& make_int,
    const MakeStr& make_str,
    const MakeVec& make_vec,
    const MakeMap& make_map
) {
    ryml::Tree tree = ryml::parse_in_arena(ryml::to_csubstr(yaml));
    return yaml_node_to_cell(tree.rootref(), make_err, make_int, make_str, make_vec, make_map);
}

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
    return yaml_string_to_cell(yaml, make_err, make_int, make_str, make_vec, make_map);
}
