import difflib
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rich import print

import yaml

from hcc import compile_path as compile_hcc_path
from hcc import dump_program as dump_hcc_program

INCLUDE_DIRECTORIES = ["include", "src", "ryml/src", "ryml/ext/c4core/src"]
INCLUDES = " ".join(f"-I{directory}" for directory in INCLUDE_DIRECTORIES)
COMPILER = "g++"
CPP_FLAGS = "-std=c++20 -Os -ffunction-sections -fdata-sections"
LINKER_FLAGS = "-Wl,--gc-sections -rdynamic -L ryml/build -lryml -ldl"
SFML_MODULE = "build/sfml.so"
SFML_MODULE_SOURCE = "lib/sfml/sfmlwrapper.cpp"
SFML_MODULE_LINKER_FLAGS = "-lsfml-graphics -lsfml-window -lsfml-system"
EXECUTABLE = "build/helix"
SOURCES = "src/helix.cpp src/builtins.cpp src/core.cpp src/utils.cpp src/ryml_interface.cpp"
OBJECT_DIRECTORY = "build/obj"
RACKET_SOURCES = ["racket/builtins.rkt", "racket/helix.rkt"]
RACKET_ENTRYPOINT = ["racket", "racket/helix.rkt"]
VALGRIND_ARGS = [
    "valgrind",
    "--leak-check=full",
    "--show-leak-kinds=all",
    "--error-exitcode=101",
]
def validate_native_module_isolation():
    """Ensures optional native libraries stay out of the core runtime binary."""
    core_sources = SOURCES.split()
    sfml_sources = [source for source in core_sources if "sfml" in source.lower()]
    if sfml_sources:
        print(f"Core runtime sources must not include SFML module sources: {sfml_sources}")
        return False

    if "sfml" in LINKER_FLAGS.lower():
        print("Core runtime linker flags must not link SFML libraries.")
        return False

    return True


def command_exists(command_name):
    return shutil.which(command_name) is not None


def compile_main():
    """Compiles the runtime sources into an executable."""
    if not validate_native_module_isolation():
        return False

    os.makedirs(OBJECT_DIRECTORY, exist_ok=True)

    object_files = []
    for source_path in SOURCES.split():
        object_name = os.path.splitext(os.path.basename(source_path))[0] + ".o"
        object_path = os.path.join(OBJECT_DIRECTORY, object_name)
        object_files.append(object_path)

        compile_command = (
            f"{COMPILER} -c {source_path} {CPP_FLAGS} {INCLUDES} -o {object_path}"
        )
        result = subprocess.run(compile_command, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Compilation failed for {source_path}.")
            if result.stderr.strip():
                print(result.stderr)
            return False

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


def compile_sfml_module():
    """Compiles the SFML wrapper into a native include module."""
    os.makedirs(os.path.dirname(SFML_MODULE), exist_ok=True)

    compile_command = (
        f"{COMPILER} -fPIC -shared {SFML_MODULE_SOURCE} {CPP_FLAGS} {INCLUDES} "
        f"-o {SFML_MODULE} {SFML_MODULE_LINKER_FLAGS}"
    )
    result = subprocess.run(compile_command, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print("SFML module compilation failed.")
        if result.stderr.strip():
            print(result.stderr)
        return False

    print("SFML module compilation successful.")
    return True


def compile_racket():
    """Precompiles the current Racket runtime modules."""
    result = subprocess.run(
        ["raco", "make", *RACKET_SOURCES],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print("Racket compilation failed.")
        if result.stdout.strip():
            print(result.stdout)
        if result.stderr.strip():
            print(result.stderr)
        return False

    print("Racket compilation successful.")
    return True

def run_clang_tidy():
    """Runs clang-tidy for static analysis and returns success status."""
    if not command_exists("clang-tidy-20"):
        print("clang-tidy-20 not found; skipping clang-tidy.")
        return True

    tidy_command = (
        "clang-tidy-20 "
        "--system-headers=0 "
        "--extra-arg=-Iinclude "
        "--extra-arg=-Isrc "
        "--extra-arg=-Iryml/src "
        "--extra-arg=-Iryml/ext/c4core/src "
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
    if not command_exists("cloc"):
        print("cloc not found; skipping line count.")
        return True

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


def run_binary_size_report():
    """Prints the final helix binary size."""
    if not os.path.exists(EXECUTABLE):
        print(f"binary size check failed: missing executable at {EXECUTABLE}")
        return False

    size_kib = os.path.getsize(EXECUTABLE) / 1024
    print(f"helix binary size: {size_kib:.2f} KiB")
    return True


def runtime_command(program_path, runtime):
    if runtime == "cpp":
        return [*VALGRIND_ARGS, f"./{EXECUTABLE}", program_path], True
    if runtime == "racket":
        return [*RACKET_ENTRYPOINT, program_path], False
    raise ValueError(f"unsupported runtime: {runtime}")


def test_result(name, passed, failure_lines=None):
    return {
        "name": name,
        "passed": passed,
        "failure_lines": [] if failure_lines is None else failure_lines,
    }


def load_yaml_fixture(test_path, test_name):
    try:
        with open(test_path, "r", encoding="utf-8") as fixture_file:
            fixture = yaml.safe_load(fixture_file)
    except yaml.YAMLError as exc:
        return None, test_result(test_name, False, [f"invalid YAML fixture: {exc}"])

    if not isinstance(fixture, dict):
        return None, test_result(test_name, False, ["fixture must be a YAML mapping"])

    return fixture, None


def normalize_fragment_list(value):
    return [value] if isinstance(value, str) else value


def evaluate_program_fixture(test_name, fixture, program_path, runtime):
    failure_lines = []

    smoke_test = fixture.get("mode") in {"smoke", "run-only"}
    expected_output = fixture.get("expected")
    expected_stdout = fixture.get("stdout", "")
    stdout_contains = normalize_fragment_list(fixture.get("stdout_contains", []))
    stdout_excludes = normalize_fragment_list(fixture.get("stdout_excludes", []))

    if not smoke_test and expected_output is None:
        return test_result(
            test_name,
            False,
            ["non-run-only fixtures must contain 'expected'"],
        )

    command, under_valgrind = runtime_command(program_path, runtime)
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )

    allowed_return_codes = (0, 101) if under_valgrind else (0,)
    if result.returncode not in allowed_return_codes:
        failure_lines.append(f"runtime error (exit code {result.returncode})")
        if result.stdout.strip():
            failure_lines.append("stdout:")
            failure_lines.append(indent_block(result.stdout.rstrip("\n")))
        if result.stderr.strip():
            failure_lines.append("stderr:")
            failure_lines.append(indent_block(result.stderr.rstrip("\n")))
        return test_result(test_name, False, failure_lines)

    if under_valgrind and result.returncode == 101:
        failure_lines.append("valgrind reported memory errors or leaks")
        if result.stdout.strip():
            failure_lines.append("stdout:")
            failure_lines.append(indent_block(result.stdout.rstrip("\n")))
        if result.stderr.strip():
            failure_lines.append("stderr:")
            failure_lines.append(indent_block(result.stderr.rstrip("\n")))
        return test_result(test_name, False, failure_lines)

    if smoke_test:
        return test_result(test_name, True)

    yaml_output, stdout_tail = split_runtime_output(result.stdout)
    if yaml_output is None:
        failure_lines.append("could not split runtime YAML output from trailing stdout")
        failure_lines.append(indent_block(result.stdout.rstrip("\n")))
        return test_result(test_name, False, failure_lines)

    if expected_stdout and not stdout_tail.startswith(expected_stdout):
        failure_lines.append("stdout mismatch")
        failure_lines.append("expected stdout:")
        failure_lines.append(indent_block(expected_stdout.rstrip("\n")))
        failure_lines.append("actual stdout:")
        failure_lines.append(indent_block(stdout_tail.rstrip("\n")))
        return test_result(test_name, False, failure_lines)

    for expected_fragment in stdout_contains:
        if expected_fragment not in stdout_tail:
            failure_lines.append(f"stdout missing fragment: {expected_fragment!r}")

    for unexpected_fragment in stdout_excludes:
        if unexpected_fragment in stdout_tail:
            failure_lines.append(f"stdout unexpectedly contained fragment: {unexpected_fragment!r}")

    if failure_lines:
        failure_lines.append("stdout tail:")
        failure_lines.append(indent_block(stdout_tail.rstrip("\n")))
        return test_result(test_name, False, failure_lines)

    try:
        actual_output = yaml.safe_load(yaml_output)
    except yaml.YAMLError as exc:
        failure_lines.append(f"invalid YAML from runtime: {exc}")
        failure_lines.append(indent_block(yaml_output.rstrip()))
        return test_result(test_name, False, failure_lines)

    if actual_output == expected_output:
        return test_result(test_name, True)

    failure_lines.append("output mismatch")
    failure_lines.append("diff:")
    failure_lines.append(indent_block(render_yaml_diff(expected_output, actual_output)))
    return test_result(test_name, False, failure_lines)


def discover_yaml_fixtures(test_directory):
    return sorted(
        os.path.join(root, file_name)
        for root, _, files in os.walk(test_directory)
        for file_name in files
        if file_name.endswith((".yaml", ".yml"))
    )


def run_fixture_tests(test_directory, run_test_case):
    if not os.path.isdir(test_directory):
        print(f"No '{test_directory}' directory found.")
        return 0

    test_paths = discover_yaml_fixtures(test_directory)
    if not test_paths:
        print(f"No YAML test fixtures found in '{test_directory}'.")
        return 0

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
            print(f"[bold red]([/bold red]{result['name']}[bold red]) -> FAILED[/bold red]")
            for line in result["failure_lines"]:
                print(f"  {line}")

    print(f"Passed: {passed}, Failed: {len(failed_results)}, Total: {len(results)}")
    return len(failed_results)


def run_tests(runtime="cpp"):
    """Discovers and runs YAML fixtures in the 'tests' directory."""
    def run_test_case(test_path):
        test_name = os.path.relpath(test_path, "tests")
        fixture, error = load_yaml_fixture(test_path, test_name)
        if error is not None:
            return error

        has_program = "program" in fixture
        has_source = "source" in fixture
        if has_program == has_source:
            return test_result(
                test_name,
                False,
                ["fixture must contain exactly one of 'program' or 'source'"],
            )

        should_delete_program = False
        if has_source:
            program_path = fixture["source"]
        else:
            program = fixture["program"]
            with tempfile.NamedTemporaryFile(
                "w",
                suffix=".yaml",
                dir=".",
                delete=False,
                encoding="utf-8",
            ) as program_file:
                yaml.safe_dump(program, program_file, sort_keys=False)
                program_path = program_file.name
                should_delete_program = True

        try:
            return evaluate_program_fixture(test_name, fixture, program_path, runtime)
        finally:
            if should_delete_program:
                os.unlink(program_path)

    return run_fixture_tests("tests", run_test_case)


def run_hcc_tests(runtime="cpp"):
    """Discovers and runs C-to-Helix fixtures in the 'hcc/tests' directory."""
    def run_test_case(test_path):
        test_name = os.path.relpath(test_path, "hcc/tests")
        fixture, error = load_yaml_fixture(test_path, test_name)
        if error is not None:
            return error

        if "c_source" not in fixture:
            return test_result(
                test_name,
                False,
                ["fixture must contain 'c_source'"],
            )

        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".c",
            dir=".",
            delete=False,
            encoding="utf-8",
        ) as source_file:
            source_file.write(fixture["c_source"])
            source_path = source_file.name

        program_path = None
        try:
            program = compile_hcc_path(Path(source_path), use_cpp=fixture.get("cpp", False))
            with tempfile.NamedTemporaryFile(
                "w",
                suffix=".yaml",
                dir=".",
                delete=False,
                encoding="utf-8",
            ) as program_file:
                program_file.write(dump_hcc_program(program))
                program_path = program_file.name

            return evaluate_program_fixture(test_name, fixture, program_path, runtime)
        except Exception as exc:
            return test_result(test_name, False, [f"hcc compile failed: {exc}"])
        finally:
            os.unlink(source_path)
            if program_path is not None:
                os.unlink(program_path)

    return run_fixture_tests("hcc/tests", run_test_case)


def render_yaml(value):
    return yaml.safe_dump(value, sort_keys=True).rstrip()


def render_yaml_diff(expected_value, actual_value):
    expected_lines = render_yaml(expected_value).splitlines()
    actual_lines = render_yaml(actual_value).splitlines()
    return "\n".join(
        difflib.unified_diff(
            expected_lines,
            actual_lines,
            fromfile="expected",
            tofile="actual",
            lineterm="",
        )
    )


def indent_block(text):
    return "\n".join(f"    {line}" for line in text.splitlines())


def split_runtime_output(stdout):
    lines = stdout.splitlines(keepends=True)
    for start_index in range(len(lines)):
        yaml_prefix = "".join(lines[start_index:])
        try:
            yaml.safe_load(yaml_prefix)
        except yaml.YAMLError:
            continue
        return yaml_prefix, "".join(lines[:start_index])
    return None, stdout


def print_usage(script_name="run.py"):
    print("usage:")
    print(f"  python3 {script_name} [--lib]                # compile C++, optional libs, tidy, cloc, and run C++ fixtures")
    print(f"  python3 {script_name} build [--lib]          # compile the C++ scaffold and optional libs only")
    print(f"  python3 {script_name} racket-build           # precompile the current Racket runtime")
    print(f"  python3 {script_name} cpp-test [--lib]       # compile C++, optional libs, and run native fixtures")
    print(f"  python3 {script_name} hcc-test [--lib]       # compile C++, optional libs, and run HCC fixtures")
    print(f"  python3 {script_name} racket-test            # run fixtures against the current Racket runtime")


def main(arguments=None, default_command="default", script_name="run.py"):
    arguments = sys.argv[1:] if arguments is None else arguments
    compile_libs = False
    filtered_arguments = []

    for argument in arguments:
        if argument == "--lib":
            compile_libs = True
            continue
        filtered_arguments.append(argument)

    if len(filtered_arguments) > 1:
        print(f"unknown arguments: {' '.join(filtered_arguments[1:])}")
        print_usage(script_name)
        sys.exit(1)

    command = filtered_arguments[0] if filtered_arguments else default_command

    if command in {"-h", "--help", "help"}:
        print_usage(script_name)
        return

    if command == "build":
        if not compile_main():
            sys.exit(1)
        if compile_libs and not compile_sfml_module():
            sys.exit(1)
        return

    if command in {"racket-build", "build-racket"}:
        if not compile_racket():
            sys.exit(1)
        return

    if command in {"racket-test", "test-racket"}:
        if not compile_racket():
            sys.exit(1)
        num_failed = run_tests("racket")
        if num_failed > 0:
            sys.exit(1)
        return

    if command in {"cpp-test", "test-cpp"}:
        if not compile_main():
            sys.exit(1)
        if compile_libs and not compile_sfml_module():
            sys.exit(1)
        num_failed = run_tests("cpp")
        if num_failed > 0:
            sys.exit(1)
        return

    if command in {"hcc-test", "test-hcc"}:
        if not compile_main():
            sys.exit(1)
        if compile_libs and not compile_sfml_module():
            sys.exit(1)
        num_failed = run_hcc_tests("cpp")
        if num_failed > 0:
            sys.exit(1)
        return

    if command != "default":
        print(f"unknown command: {command}")
        print_usage(script_name)
        sys.exit(1)

    if not compile_main():
        sys.exit(1)

    if compile_libs and not compile_sfml_module():
        sys.exit(1)

    if not run_clang_tidy():
        sys.exit(1)


    num_failed = run_tests("cpp")

    if not run_cloc():
        sys.exit(1)

    if not run_binary_size_report():
        sys.exit(1)
        
    if num_failed > 0:
        sys.exit(1)
