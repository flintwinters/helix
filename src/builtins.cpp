#include <builtins.hpp>

#include <iostream>

using namespace std;

Cell& builtin_arity_error(const char* name) {
    return Error(name);
}

Cell& add(Cell& c) {
    vector<Cell*>* values = c.vec_value();
    int total = 0;
    for (size_t index = 1; index < values->size(); ++index) {
        total += values->at(index)->as_int();
    }
    return c.own_result(new IntCell(total));
}

Cell& subtract(Cell& c) {
    vector<Cell*>* values = c.vec_value();
    if (values->size() < 2) {
        return builtin_arity_error("subtract expects at least one operand");
    }

    int total = values->at(1)->as_int();
    if (values->size() == 2) {
        return c.own_result(new IntCell(-total));
    }

    for (size_t index = 2; index < values->size(); ++index) {
        total -= values->at(index)->as_int();
    }
    return c.own_result(new IntCell(total));
}

Cell& multiply(Cell& c) {
    vector<Cell*>* values = c.vec_value();
    int total = 1;
    for (size_t index = 1; index < values->size(); ++index) {
        total *= values->at(index)->as_int();
    }
    return c.own_result(new IntCell(total));
}

Cell& divide(Cell& c) {
    vector<Cell*>* values = c.vec_value();
    if (values->size() < 3) {
        return builtin_arity_error("divide expects at least two operands");
    }

    int total = values->at(1)->as_int();
    for (size_t index = 2; index < values->size(); ++index) {
        const int divisor = values->at(index)->as_int();
        if (divisor == 0) {
            return Error("divide by zero");
        }
        total /= divisor;
    }
    return c.own_result(new IntCell(total));
}

Cell& evaluate_if_expression(Cell& expression) {
    switch (expression.type()) {
    case Cell::VEC:
    case Cell::MAP:
        return expression.call(expression);
    case Cell::INT:
    case Cell::STR:
    case Cell::FUN:
    case Cell::ANY:
    case Cell::ERROR:
        return expression;
    }
    return Error("if couldn't evaluate expression");
}

Cell& if_builtin(Cell& c) {
    vector<Cell*>* values = c.vec_value();
    if (values->size() != 4) {
        return builtin_arity_error("if expects condition, then branch, else branch");
    }

    Cell& condition = evaluate_if_expression(*values->at(1));
    if (!condition.alive) {
        return condition;
    }

    if (condition.is_truthy()) {
        return evaluate_if_expression(*values->at(2));
    }

    return evaluate_if_expression(*values->at(3));
}

Cell& printout(Cell& c) {
    cout << c.to_string() << "\n";
    return c;
}

Cell& run_all(Cell& c) {
    return run_sequence(c, 1);
}

Cell& assignment(Cell& c) {
    vector<Cell*>* values = c.vec_value();
    if (values->size() != 3) {
        return builtin_arity_error("assignment expects name and value");
    }

    
}
