#include <builtins.hpp>

#include <iostream>

using namespace std;

Cell& printout(Cell& c) {
    cout << c.to_string() << "\n";
    return c;
}
