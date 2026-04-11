#include <builtins.hpp>

#include <iostream>

using namespace std;

Cell& builtin_arity_error(const char* name) {
    return Error(name);
}

bool is_builtin_call_vector(const Cell& c) {
    const auto* values = c.vec_value();
    return c.type() == Cell::VEC && values != nullptr && !values->empty();
}

Cell& evaluate_if_expression(Cell& expression) {
    switch (expression.type()) {
    case Cell::VEC:
    case Cell::MAP:
        return expression(expression);
    case Cell::INT:
    case Cell::STR:
    case Cell::FUN:
    case Cell::ANY:
    case Cell::ERROR:
        return expression;
    }
    return Error("if couldn't evaluate expression");
}

Cell& add(Cell& c) {
    if (!is_builtin_call_vector(c)) {
        return Error("add expects a non-empty call vector");
    }

    auto* values = c.vec_value();
    int total = 0;
    for (size_t index = 1; index < values->size(); ++index) {
        total += (*values)[index]->as_int();
    }
    return c.own_result(new IntCell(total));
}

Cell& subtract(Cell& c) {
    if (!is_builtin_call_vector(c)) {
        return Error("subtract expects a non-empty call vector");
    }
    auto* values = c.vec_value();
    if (values->size() < 2) {
        return builtin_arity_error("subtract expects at least one operand");
    }

    int total = (*values)[1]->as_int();
    if (values->size() == 2) {
        return c.own_result(new IntCell(-total));
    }

    for (size_t index = 2; index < values->size(); ++index) {
        total -= (*values)[index]->as_int();
    }
    return c.own_result(new IntCell(total));
}

Cell& multiply(Cell& c) {
    if (!is_builtin_call_vector(c)) {
        return Error("multiply expects a non-empty call vector");
    }

    auto* values = c.vec_value();
    int total = 1;
    for (size_t index = 1; index < values->size(); ++index) {
        total *= (*values)[index]->as_int();
    }
    return c.own_result(new IntCell(total));
}

Cell& divide(Cell& c) {
    if (!is_builtin_call_vector(c)) {
        return Error("divide expects a non-empty call vector");
    }
    auto* values = c.vec_value();
    if (values->size() < 3) {
        return builtin_arity_error("divide expects at least two operands");
    }

    int total = (*values)[1]->as_int();
    for (size_t index = 2; index < values->size(); ++index) {
        const int divisor = (*values)[index]->as_int();
        if (divisor == 0) {
            return Error("divide by zero");
        }
        total /= divisor;
    }
    return c.own_result(new IntCell(total));
}

Cell& if_builtin(Cell& c) {
    if (!is_builtin_call_vector(c)) {
        return Error("if expects a non-empty call vector");
    }
    auto* values = c.vec_value();
    if (values->size() != 4) {
        return builtin_arity_error("if expects condition, then branch, else branch");
    }

    Cell& condition = evaluate_if_expression(*(*values)[1]);
    if (!condition.alive) {
        return condition;
    }

    if (condition.is_truthy()) {
        return evaluate_if_expression(*(*values)[2]);
    }

    return evaluate_if_expression(*(*values)[3]);
}

Cell& printout(Cell& c) {
    cout << c.to_string() << "\n";
    return c;
}
