import os
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml

INCLUDES = "-I include -I ryml/src -I ryml/ext/c4core/src"
COMPILER = "g++"
CPP_FLAGS = "-g -std=c++20"
LINKER_FLAGS = "-L ryml/build -lryml"
EXECUTABLE = "helix"
SOURCES = "src/helix.cpp src/core.cpp src/core_utils.cpp src/ryml_interface.cpp" # src/builtins.cpp"
OBJECT_DIRECTORY = "build/obj"
VALGRIND_ARGS = [
    "valgrind",
    "--leak-check=full",
    "--show-leak-kinds=all",
    "--error-exitcode=101",
]


def is_source_newer(source_path, output_path):
    return not os.path.exists(output_path) or os.path.getmtime(source_path) > os.path.getmtime(output_path)


def compile_main():
    """Compiles the runtime sources into an executable."""
    os.makedirs(OBJECT_DIRECTORY, exist_ok=True)

    object_files = []
    for source_path in SOURCES.split():
        object_name = os.path.splitext(os.path.basename(source_path))[0] + ".o"
        object_path = os.path.join(OBJECT_DIRECTORY, object_name)
        object_files.append(object_path)

        if not is_source_newer(source_path, object_path):
            continue

        compile_command = (
            f"{COMPILER} -c {source_path} {CPP_FLAGS} {INCLUDES} -o {object_path}"
        )
        result = subprocess.run(compile_command, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Compilation failed for {source_path}.")
            if result.stderr.strip():
                print(result.stderr)
            return False

    if os.path.exists(EXECUTABLE):
        should_link = any(os.path.getmtime(object_path) > os.path.getmtime(EXECUTABLE) for object_path in object_files)
    else:
        should_link = True

    if not should_link:
        print("Compilation successful.")
        return True

    link_command = (
        f"{COMPILER} {' '.join(object_files)} {CPP_FLAGS} -o {EXECUTABLE} {LINKER_FLAGS}"
    )
    result = subprocess.run(link_command, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print("Linking failed.")
        if result.stderr.strip():
            print(result.stderr)
        return False

    print("Compilation successful.")
    return True

def run_clang_tidy():
    """Runs clang-tidy for static analysis and returns success status."""
    tidy_command = (
        "clang-tidy-20 "
        "--system-headers=0 "
        "--extra-arg=-Iinclude "
        "--extra-arg=-isystemryml/src "
        "--extra-arg=-isystemryml/ext/c4core/src "
        "src/helix.cpp -- -std=c++23 -stdlib=libstdc++"
    )
    result = subprocess.run(tidy_command, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print("clang-tidy failed.")
        if result.stdout.strip():
            print(result.stdout)
        if result.stderr.strip():
            print(result.stderr)
        return False
    print("clang-tidy passed.")
    return True

def run_cloc():
    """Calculates and prints the total source line count for src and include."""
    result = subprocess.run(
        ["cloc", "-q", "src", "include"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("count lines of code failed.")
        if result.stdout.strip():
            print(result.stdout.rstrip("\n"))
        if result.stderr.strip():
            print(result.stderr.rstrip("\n"))
        return False

    code_line_count = None
    for line in result.stdout.splitlines():
        if line.startswith("SUM:"):
            print("current LOC:", line.split()[-1])
            return True
        columns = line.split()
        if len(columns) == 5 and columns[0] == "C++":
            code_line_count = columns[-1]

    if code_line_count is not None:
        print("current LOC:", code_line_count)
        return True

    print("count lines of code failed.")
    print("missing SUM line in cloc output")
    return False

def run_tests():
    """Discovers and runs YAML fixtures in the 'tests' directory."""
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

    def run_test_case(test_path):
        test_name = os.path.relpath(test_path, "tests")
        failure_lines = []

        try:
            with open(test_path, "r", encoding="utf-8") as fixture_file:
                fixture = yaml.safe_load(fixture_file)
        except yaml.YAMLError as exc:
            return {
                "name": test_name,
                "passed": False,
                "failure_lines": [f"invalid YAML fixture: {exc}"],
            }

        if not isinstance(fixture, dict) or "program" not in fixture:
            return {
                "name": test_name,
                "passed": False,
                "failure_lines": ["fixture must contain 'program'"],
            }

        program = fixture["program"]
        smoke_test = fixture.get("mode") in {"smoke", "run-only"}
        expected_output = fixture.get("expected")
        expected_stdout = fixture.get("stdout", "")
        stdout_contains = fixture.get("stdout_contains", [])
        stdout_excludes = fixture.get("stdout_excludes", [])

        if isinstance(stdout_contains, str):
            stdout_contains = [stdout_contains]
        if isinstance(stdout_excludes, str):
            stdout_excludes = [stdout_excludes]

        if not smoke_test and expected_output is None:
            return {
                "name": test_name,
                "passed": False,
                "failure_lines": ["non-run-only fixtures must contain 'expected'"],
            }

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
                [*VALGRIND_ARGS, f"./{EXECUTABLE}", program_path],
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            os.unlink(program_path)

        if result.returncode not in (0, 101):
            failure_lines.append(f"runtime error (exit code {result.returncode})")
            if result.stdout.strip():
                failure_lines.append("stdout:")
                failure_lines.append(indent_block(result.stdout.rstrip("\n")))
            if result.stderr.strip():
                failure_lines.append("stderr:")
                failure_lines.append(indent_block(result.stderr.rstrip("\n")))
            return {
                "name": test_name,
                "passed": False,
                "failure_lines": failure_lines,
            }

        if result.returncode == 101:
            failure_lines.append("valgrind reported memory errors or leaks")
            if result.stdout.strip():
                failure_lines.append("stdout:")
                failure_lines.append(indent_block(result.stdout.rstrip("\n")))
            if result.stderr.strip():
                failure_lines.append("stderr:")
                failure_lines.append(indent_block(result.stderr.rstrip("\n")))
            return {
                "name": test_name,
                "passed": False,
                "failure_lines": failure_lines,
            }

        if smoke_test:
            return {
                "name": test_name,
                "passed": True,
                "failure_lines": [],
            }

        yaml_output, stdout_tail = split_runtime_output(result.stdout)
        if yaml_output is None:
            failure_lines.append("could not split runtime YAML output from trailing stdout")
            failure_lines.append(indent_block(result.stdout.rstrip("\n")))
            return {
                "name": test_name,
                "passed": False,
                "failure_lines": failure_lines,
            }

        if expected_stdout and not stdout_tail.startswith(expected_stdout):
            failure_lines.append("stdout mismatch")
            failure_lines.append("expected stdout:")
            failure_lines.append(indent_block(expected_stdout.rstrip("\n")))
            failure_lines.append("actual stdout:")
            failure_lines.append(indent_block(stdout_tail.rstrip("\n")))
            return {
                "name": test_name,
                "passed": False,
                "failure_lines": failure_lines,
            }

        for expected_fragment in stdout_contains:
            if expected_fragment not in stdout_tail:
                failure_lines.append(f"stdout missing fragment: {expected_fragment!r}")

        for unexpected_fragment in stdout_excludes:
            if unexpected_fragment in stdout_tail:
                failure_lines.append(f"stdout unexpectedly contained fragment: {unexpected_fragment!r}")

        if failure_lines:
            failure_lines.append("stdout tail:")
            failure_lines.append(indent_block(stdout_tail.rstrip("\n")))
            return {
                "name": test_name,
                "passed": False,
                "failure_lines": failure_lines,
            }

        try:
            actual_output = yaml.safe_load(yaml_output)
        except yaml.YAMLError as exc:
            failure_lines.append(f"invalid YAML from runtime: {exc}")
            failure_lines.append(indent_block(yaml_output.rstrip()))
            return {
                "name": test_name,
                "passed": False,
                "failure_lines": failure_lines,
            }

        if actual_output == expected_output:
            return {
                "name": test_name,
                "passed": True,
                "failure_lines": [],
            }

        failure_lines.append("output mismatch")
        failure_lines.append("expected:")
        failure_lines.append(indent_block(render_yaml(expected_output)))
        failure_lines.append("actual:")
        failure_lines.append(indent_block(render_yaml(actual_output)))
        return {
            "name": test_name,
            "passed": False,
            "failure_lines": failure_lines,
        }

    max_workers = min(len(test_paths), os.cpu_count() or 1)
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(run_test_case, test_path): test_path
            for test_path in test_paths
        }
        for future in as_completed(future_map):
            results.append(future.result())

    results.sort(key=lambda result: result["name"])
    passed = sum(1 for result in results if result["passed"])
    failed_results = [result for result in results if not result["passed"]]

    if failed_results:
        print("Test failures:")
        for result in failed_results:
            print(f"[{result['name']}] FAILED")
            for line in result["failure_lines"]:
                print(f"  {line}")

    print(f"Passed: {passed}, Failed: {len(failed_results)}, Total: {len(results)}")
    return len(failed_results)


def render_yaml(value):
    return yaml.safe_dump(value, sort_keys=True).rstrip()


def indent_block(text):
    return "\n".join(f"    {line}" for line in text.splitlines())


def split_runtime_output(stdout):
    lines = stdout.splitlines(keepends=True)
    for line_count in range(len(lines), 0, -1):
        yaml_prefix = "".join(lines[:line_count])
        try:
            yaml.safe_load(yaml_prefix)
        except yaml.YAMLError:
            continue
        return yaml_prefix, "".join(lines[line_count:])
    return None, stdout

def main():
    if not compile_main():
        sys.exit(1)
    
    if not run_clang_tidy():
        sys.exit(1)

    if not run_cloc():
        sys.exit(1)

    num_failed = run_tests()

    if num_failed > 0:
        sys.exit(1)

        
if __name__ == "__main__":
    main()
