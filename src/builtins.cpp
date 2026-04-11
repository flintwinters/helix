#include <builtins.hpp>

#include <iostream>

using namespace std;

Cell& builtin_arity_error(const char* name) {
    return Error(name);
}

bool is_builtin_call_vector(const Cell& c) {
    return c.t == Cell::VEC && c.v != nullptr && !c.v->empty();
}

Cell& evaluate_if_expression(Cell& expression) {
    switch (expression.t) {
    case Cell::VEC:
    case Cell::MAP:
        return expression(expression);
    case Cell::INT:
    case Cell::STR:
    case Cell::FUN:
    case Cell::ANY:
        return expression;
    }
    return Error("if couldn't evaluate expression");
}

bool is_truthy_value(const Cell& cell) {
    switch (cell.t) {
    case Cell::INT:
        return cell.i != 0;
    case Cell::STR:
        return cell.s != nullptr && !cell.s->empty();
    case Cell::FUN:
        return true;
    case Cell::VEC:
        return cell.v != nullptr && !cell.v->empty();
    case Cell::MAP:
        return cell.m != nullptr && !cell.m->empty();
    case Cell::ANY:
        return cell.a != nullptr;
    }
    return false;
}

Cell& add(Cell& c) {
    if (!is_builtin_call_vector(c)) {
        return Error("add expects a non-empty call vector");
    }

    int total = 0;
    for (size_t index = 1; index < c.v->size(); ++index) {
        total += (*c.v)[index]->i;
    }
    return allocate_in_arena(new Cell(total));
}

Cell& subtract(Cell& c) {
    if (!is_builtin_call_vector(c)) {
        return Error("subtract expects a non-empty call vector");
    }
    if (c.v->size() < 2) {
        return builtin_arity_error("subtract expects at least one operand");
    }

    int total = (*c.v)[1]->i;
    if (c.v->size() == 2) {
        return allocate_in_arena(new Cell(-total));
    }

    for (size_t index = 2; index < c.v->size(); ++index) {
        total -= (*c.v)[index]->i;
    }
    return allocate_in_arena(new Cell(total));
}

Cell& multiply(Cell& c) {
    if (!is_builtin_call_vector(c)) {
        return Error("multiply expects a non-empty call vector");
    }

    int total = 1;
    for (size_t index = 1; index < c.v->size(); ++index) {
        total *= (*c.v)[index]->i;
    }
    return allocate_in_arena(new Cell(total));
}

Cell& divide(Cell& c) {
    if (!is_builtin_call_vector(c)) {
        return Error("divide expects a non-empty call vector");
    }
    if (c.v->size() < 3) {
        return builtin_arity_error("divide expects at least two operands");
    }

    int total = (*c.v)[1]->i;
    for (size_t index = 2; index < c.v->size(); ++index) {
        const int divisor = (*c.v)[index]->i;
        if (divisor == 0) {
            return Error("divide by zero");
        }
        total /= divisor;
    }
    return allocate_in_arena(new Cell(total));
}

Cell& if_builtin(Cell& c) {
    if (!is_builtin_call_vector(c)) {
        return Error("if expects a non-empty call vector");
    }
    if (c.v->size() != 4) {
        return builtin_arity_error("if expects condition, then branch, else branch");
    }

    Cell& condition = evaluate_if_expression(*(*c.v)[1]);
    if (!condition.alive) {
        return condition;
    }

    if (is_truthy_value(condition)) {
        return evaluate_if_expression(*(*c.v)[2]);
    }

    return evaluate_if_expression(*(*c.v)[3]);
}

Cell& printout(Cell& c) {
    cout << c.to_string() << "\n";
    return c;
}
