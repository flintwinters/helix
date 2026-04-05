import os
import subprocess
import sys
import tempfile

import yaml

INCLUDES = "-I include -I ryml/src -I ryml/ext/c4core/src"
COMPILER = "g++"
CPP_FLAGS = "-g -std=c++20"
LINKER_FLAGS = "-L ryml/build -lryml"
EXECUTABLE = "helix"
SOURCES = "src/helix.cpp src/core.cpp src/ryml_interface.cpp src/builtins.cpp"

def compile_main():
    """Compiles the runtime sources into an executable."""
    print("--- Compiling helix sources ---")
    compile_command = (
        f"{COMPILER} {SOURCES} {CPP_FLAGS} -o {EXECUTABLE} "
        f"{INCLUDES} {LINKER_FLAGS}"
    )
    print(f"$ {compile_command}")
    result = subprocess.run(compile_command, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print("\033[1;31mCompilation failed!\033[0m")
        print(result.stderr)
        return False
    print("\033[1;32mCompilation successful.\033[0m")
    return True

def run_clang_tidy():
    """Runs clang-tidy for static analysis and returns success status."""
    print("\n--- Running clang-tidy ---")
    tidy_command = (
        "clang-tidy-20 "
        "--system-headers=0 "
        "--extra-arg=-Iinclude "
        "--extra-arg=-isystemryml/src "
        "--extra-arg=-isystemryml/ext/c4core/src "
        "src/helix.cpp -- -std=c++23 -stdlib=libstdc++"
    )
    print(f"$ {tidy_command}")
    result = subprocess.run(tidy_command, shell=True)
    if result.returncode != 0:
        print("clang-tidy found issues.")
        return False
    else:
        print("clang-tidy checks passed.")
        return True

def run_tests():
    """Discovers and runs YAML fixtures in the 'tests' directory."""
    print("\n--- Running Tests ---")
    passed = 0
    failed = 0

    if not os.path.isdir("tests"):
        print("No 'tests' directory found.")
        return 0

    test_paths = sorted(
        os.path.join(root, file_name)
        for root, _, files in os.walk("tests")
        for file_name in files
        if file_name.endswith((".yaml", ".yml"))
    )

    if not test_paths:
        print("No YAML test fixtures found.")
        return 0

    for test_path in test_paths:
        test_name = os.path.relpath(test_path, "tests")

        try:
            with open(test_path, "r", encoding="utf-8") as fixture_file:
                fixture = yaml.safe_load(fixture_file)
        except yaml.YAMLError as exc:
            print(f"[{test_name}] FAILED (invalid YAML fixture)")
            print(f"  {exc}")
            failed += 1
            continue

        if not isinstance(fixture, dict) or "program" not in fixture or "expected" not in fixture:
            print(f"[{test_name}] FAILED (fixture must contain 'program' and 'expected')")
            failed += 1
            continue

        program = fixture["program"]
        expected_output = fixture["expected"]
        expected_stdout = fixture.get("stdout", "")

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".yaml",
            dir=".",
            delete=False,
            encoding="utf-8",
        ) as program_file:
            yaml.safe_dump(program, program_file, sort_keys=False)
            program_path = program_file.name

        try:
            result = subprocess.run(
                [f"./{EXECUTABLE}", program_path],
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            os.unlink(program_path)

        if result.returncode != 0:
            print(f"[{test_name}] FAILED (runtime error)")
            print(f"  Stderr: {result.stderr.strip()}")
            failed += 1
            continue

        if not result.stdout.startswith(expected_stdout):
            print(f"[{test_name}] FAILED (stdout mismatch)")
            print("  Expected stdout:")
            print(indent_block(expected_stdout.rstrip("\n")))
            print("  Actual stdout:")
            print(indent_block(result.stdout.rstrip("\n")))
            failed += 1
            continue

        yaml_output = result.stdout[len(expected_stdout):]

        try:
            actual_output = yaml.safe_load(yaml_output)
        except yaml.YAMLError as exc:
            print(f"[{test_name}] FAILED (invalid YAML from runtime)")
            print(f"  {exc}")
            print(indent_block(yaml_output.rstrip()))
            failed += 1
            continue

        if actual_output == expected_output:
            print(f"[{test_name}] PASSED")
            passed += 1
        else:
            print(f"[{test_name}] FAILED (output mismatch)")
            print("  Expected:")
            print(indent_block(render_yaml(expected_output)))
            print("  Actual:")
            print(indent_block(render_yaml(actual_output)))
            failed += 1

    print("\n--- Test Summary ---")
    print(f"Passed: {passed}, Failed: {failed}, Total: {passed + failed}")
    return failed


def render_yaml(value):
    return yaml.safe_dump(value, sort_keys=True).rstrip()


def indent_block(text):
    return "\n".join(f"    {line}" for line in text.splitlines())

def run_valgrind():
    """Runs valgrind to check for memory leaks during bootstrap."""
    print("\n--- Running Valgrind on bootstrap ---")
    # We run without arguments to test memory usage of bootstrap and shutdown.
    # It will exit with 1, which is expected.
    valgrind_command = f"valgrind -s --leak-check=full --show-leak-kinds=all ./{EXECUTABLE}"
    print(f"$ {valgrind_command}")
    subprocess.run(valgrind_command, shell=True, capture_output=True, text=True)
    print("Valgrind check complete.")

def main():
    if not compile_main():
        sys.exit(1)
    
    if not run_clang_tidy():
        sys.exit(1)
    
    # num_failed = run_tests()

    # if num_failed > 0:
    #     sys.exit(1)

        
if __name__ == "__main__":
    main()
