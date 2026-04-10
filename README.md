# helix

Helix is a small experimental runtime written in C++. It loads YAML input,
builds an internal cell graph, and evaluates that graph through a compact
runtime model centered around maps, vectors, strings, numbers, and builtins.

The project is built with the existing Python build script. Running
`python3 build.py` compiles the `helix` binary and executes the current test
suite through the repository's scaffolded test flow.

For direct local execution, run `./helix test.yaml`. The repository also keeps
its main development workflow simple: edit the runtime, rebuild with
`python3 build.py`, and use the same command to confirm that the test cases
still execute cleanly.
