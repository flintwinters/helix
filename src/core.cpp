#include <charconv>
#include <cstdint>
#include <functional>
#include <memory>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include <c4/yml/node.hpp>

using namespace std;

class Cell;
using CellPtr = shared_ptr<Cell>;
using ConstCellPtr = shared_ptr<const Cell>;

struct Cell {
    enum class Type {
        base,
        map,
        vec,
        integer,
        string,
        function,
        signal,
        return_signal,
        error_signal,
    };
    
    bool callable = false;
    
    Cell() = default;
    Cell(Type initial_type) : type(initial_type) {}
    Cell(const Cell&) = default;
    Cell(Cell&&) = default;
    Cell& operator=(const Cell&) = default;
    Cell& operator=(Cell&&) = default;
    virtual ~Cell() = default;
    
    Type type {Type::base};
    virtual bool is_signal() const noexcept { return false; }
    virtual size_t size() const noexcept { return -1; }
    virtual CellPtr call(const vector<CellPtr>& arguments, CellPtr current_vm) const {
        throw logic_error("Cell is not callable");
    }
};

struct MapCell final : public Cell {
    MapCell() : Cell(Type::map) {}
    MapCell(unordered_map<string, CellPtr> fields) : Cell(Type::map), value(move(fields)) {}
    
    size_t size() const noexcept override { return value.size(); }
    
    unordered_map<string, CellPtr> value {};
};

struct VecCell final : public Cell {
    VecCell() : Cell(Type::vec) {}
    VecCell(vector<CellPtr> elements) : Cell(Type::vec), value(move(elements)) {}
    
    size_t size() const noexcept override { return value.size(); }
    
    vector<CellPtr> value {};
};

struct IntCell final : public Cell {
    IntCell(int64_t initial_value) : Cell(Type::integer), value(initial_value) {}
    
    int64_t value {};
};

struct StrCell final : public Cell {
    StrCell() : Cell(Type::string) {}
    StrCell(string initial_value) : Cell(Type::string), value(move(initial_value)) {}
    
    size_t size() const noexcept override { return value.size(); }
    
    string value {};
};

struct FunCell final : public Cell {
    using Implementation = function<CellPtr(const vector<CellPtr>&, CellPtr)>;
    
    FunCell() : Cell(Type::function) {}
    FunCell(Implementation implementation) : Cell(Type::function), value(move(implementation)) {}
    
    bool callable = true;
    CellPtr call(const vector<CellPtr>& arguments, CellPtr current_vm) const override {
        if (!value) {
            throw logic_error("FunCell has no implementation");
        }
        return value(arguments, move(current_vm));
    }
    
    Implementation value {};
};

struct SigCell : public Cell {
    SigCell() : Cell(Type::signal) {}
    SigCell(CellPtr initial_value) : Cell(Type::signal), value(move(initial_value)) {}
    SigCell(Type initial_type, CellPtr initial_value = nullptr)
    : Cell(initial_type), value(move(initial_value)) {}
    
    bool is_signal() const noexcept override { return true; }
    
    CellPtr value {};
};

struct RetCell final : public SigCell {
    RetCell() : SigCell(Type::return_signal) {}
    RetCell(CellPtr initial_value) : SigCell(Type::return_signal, move(initial_value)) {}
};

struct ErrCell final : public SigCell {
    ErrCell() : SigCell(Type::error_signal) {}
    ErrCell(string initial_message, CellPtr initial_value = nullptr)
    : SigCell(Type::error_signal, move(initial_value)), message(move(initial_message)) {}
    
    string message {};
};

inline string ryml_text_to_string(c4::csubstr text)
{
    return {text.str, text.len};
}

inline CellPtr scalar_cell_from_ryml(c4::csubstr scalar)
{
    const string text = ryml_text_to_string(scalar);
    int64_t integer_value = 0;
    const char* begin = text.data();
    const char* end = begin + text.size();
    if(!text.empty())
    {
        const auto result = from_chars(begin, end, integer_value);
        if(result.ec == errc{} && result.ptr == end)
        {
            return make_shared<IntCell>(integer_value);
        }
    }
    return make_shared<StrCell>(text);
}

inline CellPtr cell_from_ryml_node(c4::yml::ConstNodeRef node)
{
    while((node.is_stream() || node.is_doc()) && node.has_children())
    {
        node = node.first_child();
    }

    if(node.is_map())
    {
        unordered_map<string, CellPtr> fields {};
        for(const auto child : node.children())
        {
            fields.emplace(ryml_text_to_string(child.key()), cell_from_ryml_node(child));
        }
        return make_shared<MapCell>(move(fields));
    }

    if(node.is_seq())
    {
        vector<CellPtr> elements {};
        elements.reserve(static_cast<size_t>(node.num_children()));
        for(const auto child : node.children())
        {
            elements.push_back(cell_from_ryml_node(child));
        }
        return make_shared<VecCell>(move(elements));
    }

    if(node.has_val())
    {
        return scalar_cell_from_ryml(node.val());
    }

    return make_shared<StrCell>();
}
