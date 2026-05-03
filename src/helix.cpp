#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>

#include <ryml_interface.hpp>

static string render_show_output(ConstCellPtr cell)
{
    string rendered = emit_yaml_from_cell(cell);
    if(cell && (cell->type == Cell::Type::integer || cell->type == Cell::Type::string))
    {
        rendered += "...\n";
    }
    return rendered;
}

static bool is_null_cell(ConstCellPtr cell)
{
    if(!cell)
    {
        return true;
    }

    if(cell->type != Cell::Type::string)
    {
        return false;
    }

    const auto& str_cell = static_cast<const StrCell&>(*cell);
    return str_cell.value == "null";
}

static int64_t expect_int_cell(ConstCellPtr cell, const char* who)
{
    if(!cell || cell->type != Cell::Type::integer)
    {
        throw runtime_error(string(who) + " expects integer arguments");
    }
    return static_cast<const IntCell&>(*cell).value;
}

static CellPtr evaluate_cell(CellPtr node, MapCell& root);

static CellPtr evaluate_form(const VecCell& form, MapCell& root)
{
    if(form.value.empty())
    {
        throw runtime_error("cannot evaluate an empty vector");
    }

    const CellPtr actor = form.value.front();
    if(!actor || actor->type != Cell::Type::string)
    {
        throw runtime_error("vector actor did not resolve to a builtin");
    }

    const auto& actor_name = static_cast<const StrCell&>(*actor).value;

    if(actor_name == "show")
    {
        if(form.value.size() != 2)
        {
            throw runtime_error("show expects exactly 1 argument");
        }

        const CellPtr value = evaluate_cell(form.value[1], root);
        cout << render_show_output(value);
        return nullptr;
    }

    if(actor_name == "add")
    {
        if(form.value.size() != 3)
        {
            throw runtime_error("add expects exactly 2 arguments");
        }

        const int64_t left = expect_int_cell(evaluate_cell(form.value[1], root), "add");
        const int64_t right = expect_int_cell(evaluate_cell(form.value[2], root), "add");
        return make_shared<IntCell>(left + right);
    }

    throw runtime_error("vector actor did not resolve to a builtin");
}

static CellPtr evaluate_cell(CellPtr node, MapCell& root)
{
    if(!node)
    {
        return nullptr;
    }

    if(node->type == Cell::Type::vec)
    {
        return evaluate_form(static_cast<const VecCell&>(*node), root);
    }

    if(node->type == Cell::Type::string)
    {
        const auto& name = static_cast<const StrCell&>(*node).value;
        const auto found = root.value.find(name);
        if(found != root.value.end())
        {
            return found->second;
        }
    }

    return node;
}

static void run_main(CellPtr root_cell)
{
    if(!root_cell || root_cell->type != Cell::Type::map)
    {
        throw runtime_error("top-level YAML document must be a mapping");
    }

    auto& root = static_cast<MapCell&>(*root_cell);
    const auto main_it = root.value.find("main");
    if(main_it == root.value.end())
    {
        throw runtime_error("program is missing a main entrypoint");
    }

    CellPtr result = nullptr;
    if(is_null_cell(main_it->second))
    {
        result = main_it->second;
    }
    else
    {
        result = evaluate_cell(main_it->second, root);
    }

    auto state = make_shared<MapCell>();
    state->value["status"] = make_shared<StrCell>("finished");
    state->value["frames"] = make_shared<VecCell>();
    if(result)
    {
        state->value["result"] = move(result);
    }
    root.value["state"] = move(state);
}

int main(int argc, char* argv[])
{
    if(argc != 2)
    {
        std::cerr << "usage: ./helix <program.yaml>\n";
        return 1;
    }

    try
    {
        CellPtr root_cell = load_root_cell_from_yaml_file(argv[1]);
        run_main(root_cell);
        std::cout << emit_yaml_from_cell(root_cell);
    }
    catch(const exception& error)
    {
        std::cerr << "error: " << error.what() << '\n';
        return 1;
    }

    return 0;
}
