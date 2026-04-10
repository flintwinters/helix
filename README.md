# Helix

Helix is a small experimental runtime written in C++. It loads YAML input,
builds an internal cell graph, and evaluates that graph through a compact
runtime model centered around maps, vectors, strings, numbers, and builtin functions.

Python is used as a buildscript. `python3 build.py` compiles the `helix` binary and executes the test suite with valgrind.

```
├── helix
├── README.md
├── build.py
├── include
│   ├── builtins.hpp
│   ├── core.hpp
│   └── ryml_interface.hpp
├── src
│   ├── builtins.cpp
│   ├── core.cpp
│   ├── helix.cpp
│   └── ryml_interface.cpp
└── tests
    └── arithmetic
        ├── add.yaml
        ├── divide.yaml
        ├── multiply.yaml
        ├── printout.yaml
        └── subtract.yaml
```