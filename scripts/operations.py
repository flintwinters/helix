import difflib
import http.client
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rich import print

import yaml

from hcc import compile_path as compile_hcc_path
from hcc import dump_program as dump_hcc_program

INCLUDE_DIRECTORIES = ["include", "src", "ryml/src", "ryml/ext/c4core/src"]
INCLUDES = " ".join(f"-I{directory}" for directory in INCLUDE_DIRECTORIES)
COMPILER = "g++"
BASE_CPP_FLAGS = "-std=c++20 -ffunction-sections -fdata-sections"
SIZE_OPTIMIZATION_FLAGS = "-Os -flto -fno-rtti"
RYML_SOURCE_DIRECTORY = "ryml"
RYML_BUILD_ROOT = "build/dependencies"
LINKER_FLAGS = "-Wl,--gc-sections"
DYNAMIC_LIBRARY_LINKER_FLAGS = "-rdynamic -ldl"
SIZE_OPTIMIZATION_LINKER_FLAGS = "-flto -s"
SFML_MODULE = "build/sfml.so"
SFML_MODULE_SOURCE = "lib/sfml/sfmlwrapper.cpp"
SFML_MODULE_LINKER_FLAGS = "-lsfml-graphics -lsfml-window -lsfml-system"
WEBDEMO_MODULE = "webdemo/web.so"
WEBDEMO_MODULE_SOURCE = "webdemo/web.cpp"
WEBDEMO_TEST_PROGRAM = "webdemo/server_test.yaml"
EXECUTABLE = "build/helix"
SOURCES = "src/helix.cpp src/builtins.cpp src/core.cpp src/utils.cpp src/ryml_interface.cpp"
OBJECT_DIRECTORY = "build/obj"
VALGRIND_ARGS = [
    "valgrind",
    "--leak-check=full",
    "--show-leak-kinds=all",
    "--error-exitcode=101",
]
PRESENT_SENTINEL = "<present>"
LOCATION_FIELD = "location"
POSITION_FIELDS = frozenset({"line", "column"})
FIXTURE_ASSET_DIRECTORY = "assets"


def comparable_output(value, ignored_fields):
    if isinstance(value, dict):
        return {
            key: comparable_output(child, ignored_fields)
            for key, child in value.items()
            if key not in ignored_fields
        }

    if isinstance(value, list):
        return [comparable_output(child, ignored_fields) for child in value]

    return value


def expected_output_matches(expected_value, actual_value, ignored_fields=frozenset()):
    if expected_value == PRESENT_SENTINEL:
        return True

    if isinstance(expected_value, dict):
        if not isinstance(actual_value, dict):
            return False

        for key, expected_child in expected_value.items():
            if key in ignored_fields:
                continue
            if key not in actual_value:
                return False
            if not expected_output_matches(expected_child, actual_value[key], ignored_fields):
                return False

        return set(actual_value) - ignored_fields == set(expected_value) - ignored_fields

    if isinstance(expected_value, list):
        if not isinstance(actual_value, list) or len(actual_value) != len(expected_value):
            return False

        return all(
            expected_output_matches(expected_child, actual_child, ignored_fields)
            for expected_child, actual_child in zip(expected_value, actual_value)
        )

    return actual_value == expected_value


def relax_expected_error_positions(value):
    if isinstance(value, dict):
        relaxed = {
            key: relax_expected_error_positions(child)
            for key, child in value.items()
        }
        location = relaxed.get(LOCATION_FIELD)
        if isinstance(location, dict):
            for key in POSITION_FIELDS:
                if key in location:
                    location[key] = PRESENT_SENTINEL
        return relaxed

    if isinstance(value, list):
        return [relax_expected_error_positions(child) for child in value]

    return value


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


def production_build_features(dynamic_libraries, cpp_linenums, optimize_size):
    """Resolve feature switches implied by the production size profile."""
    if optimize_size:
        return False, False
    return dynamic_libraries, cpp_linenums


def compile_cpp_flags(dynamic_libraries, optimize_size=False, cpp_linenums=True):
    dynamic_libraries, cpp_linenums = production_build_features(
        dynamic_libraries,
        cpp_linenums,
        optimize_size,
    )
    enabled = "1" if dynamic_libraries else "0"
    cpp_linenums_enabled = "1" if cpp_linenums else "0"
    flags = BASE_CPP_FLAGS
    if optimize_size:
        flags = f"{flags} {SIZE_OPTIMIZATION_FLAGS}"
    return f"{flags} -DHELIX_ENABLE_DYNAMIC_LIBRARIES={enabled} -DHELIX_ENABLE_CPP_LINENUMS={cpp_linenums_enabled}"


def compile_linker_flags(dynamic_libraries, optimize_size=False):
    dynamic_libraries, _ = production_build_features(
        dynamic_libraries,
        cpp_linenums=False,
        optimize_size=optimize_size,
    )
    flags = f"{LINKER_FLAGS} -L {ryml_build_directory(optimize_size)} -lryml"
    if dynamic_libraries:
        flags = f"{flags} {DYNAMIC_LIBRARY_LINKER_FLAGS}"
    if optimize_size:
        flags = f"{flags} {SIZE_OPTIMIZATION_LINKER_FLAGS}"
    return flags


def command_exists(command_name):
    return shutil.which(command_name) is not None


def ryml_build_directory(optimize_size=False):
    profile = "min-size" if optimize_size else "release"
    return f"{RYML_BUILD_ROOT}/ryml-{profile}"


def configure_ryml(optimize_size=False):
    """Build ryml with flags matching the selected Helix build profile."""
    build_type = "MinSizeRel" if optimize_size else "Release"
    build_directory = ryml_build_directory(optimize_size)
    configure_command = [
        "cmake",
        "-S",
        RYML_SOURCE_DIRECTORY,
        "-B",
        build_directory,
        f"-DCMAKE_BUILD_TYPE={build_type}",
        f"-DCMAKE_INTERPROCEDURAL_OPTIMIZATION={'ON' if optimize_size else 'OFF'}",
        f"-DRYML_SHORT_ERR_MSG={'ON' if optimize_size else 'OFF'}",
    ]
    if optimize_size:
        configure_command.append(
            "-DCMAKE_CXX_FLAGS_MINSIZEREL=-Os -DNDEBUG -flto -fno-rtti"
        )
    else:
        configure_command.append("-DCMAKE_CXX_FLAGS_RELEASE=-O3 -DNDEBUG")

    commands = (
        (configure_command, "ryml configuration failed."),
        (
            [
                "cmake",
                "--build",
                build_directory,
                "--config",
                build_type,
            ],
            "ryml compilation failed.",
        ),
    )
    for command, failure_message in commands:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            print(failure_message)
            if result.stderr.strip():
                print(result.stderr)
            return False
    return True


def compile_main(dynamic_libraries=True, optimize_size=False, cpp_linenums=True):
    """Compiles the runtime sources into an executable."""
    if not validate_native_module_isolation():
        return False
    if not configure_ryml(optimize_size):
        return False

    os.makedirs(OBJECT_DIRECTORY, exist_ok=True)
    cpp_flags = compile_cpp_flags(dynamic_libraries, optimize_size, cpp_linenums)

    object_files = []
    for source_path in SOURCES.split():
        object_name = os.path.splitext(os.path.basename(source_path))[0] + ".o"
        object_path = os.path.join(OBJECT_DIRECTORY, object_name)
        object_files.append(object_path)

        compile_command = (
            f"{COMPILER} -c {source_path} {cpp_flags} {INCLUDES} -o {object_path}"
        )
        result = subprocess.run(compile_command, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Compilation failed for {source_path}.")
            if result.stderr.strip():
                print(result.stderr)
            return False

    link_command = (
        f"{COMPILER} {' '.join(object_files)} {cpp_flags} -o {EXECUTABLE} {compile_linker_flags(dynamic_libraries, optimize_size)}"
    )
    result = subprocess.run(link_command, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print("Linking failed.")
        if result.stderr.strip():
            print(result.stderr)
        return False

    print("Compilation successful.")
    return True


def compile_native_module(module, source, linker_flags, label, optimize_size=False, cpp_linenums=True):
    """Compile an isolated Helix native include module."""
    os.makedirs(os.path.dirname(module), exist_ok=True)
    cpp_flags = compile_cpp_flags(
        dynamic_libraries=True,
        optimize_size=optimize_size,
        cpp_linenums=cpp_linenums,
    )

    compile_command = (
        f"{COMPILER} -fPIC -shared {source} {cpp_flags} {INCLUDES} "
        f"-o {module} {linker_flags}"
    )
    result = subprocess.run(compile_command, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"{label} module compilation failed.")
        if result.stderr.strip():
            print(result.stderr)
        return False

    print(f"{label} module compilation successful.")
    return True


def compile_sfml_module(optimize_size=False, cpp_linenums=True):
    """Compiles the SFML wrapper into a native include module."""
    return compile_native_module(
        SFML_MODULE,
        SFML_MODULE_SOURCE,
        SFML_MODULE_LINKER_FLAGS,
        "SFML",
        optimize_size,
        cpp_linenums,
    )


def compile_webdemo_module():
    """Compiles the loopback HTTP capability used by the Helix web demo."""
    return compile_native_module(
        WEBDEMO_MODULE,
        WEBDEMO_MODULE_SOURCE,
        "",
        "Web demo",
    )


def build_webdemo():
    """Build the runtime and the native module required by webdemo/server.yaml."""
    return compile_main(dynamic_libraries=True) and compile_webdemo_module()


def request_webdemo(path):
    """Request one path from the bounded loopback web-demo process."""
    connection = http.client.HTTPConnection("127.0.0.1", 18080, timeout=1)
    connection.request("GET", path)
    response = connection.getresponse()
    result = response.status, response.read().decode("utf-8")
    connection.close()
    return result


def run_webdemo_test():
    """Exercise the bounded Helix web server through its declared endpoint objects."""
    if not build_webdemo():
        return False

    server = subprocess.Popen(
        [EXECUTABLE, WEBDEMO_TEST_PROGRAM],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(100):
            try:
                responses = [
                    request_webdemo("/"),
                    request_webdemo("/health"),
                    request_webdemo("/missing"),
                ]
                break
            except ConnectionRefusedError:
                time.sleep(0.01)
        else:
            print("Web demo did not begin listening on 127.0.0.1:18080.")
            return False

        stdout, stderr = server.communicate(timeout=5)
        expected_responses = [
            (200, "Helix webdemo root response"),
            (200, "Helix webdemo health response"),
            (404, "Not found"),
        ]
        if responses != expected_responses:
            print(f"Unexpected web demo responses: {responses!r}")
            return False
        if server.returncode != 0:
            print("Web demo server exited unsuccessfully.")
            if stderr.strip():
                print(stderr)
            if stdout.strip():
                print(stdout)
            return False
    except subprocess.TimeoutExpired:
        print("Web demo server did not exit after its one configured request.")
        return False
    finally:
        if server.poll() is None:
            server.terminate()
            server.communicate()

    print("Web demo test passed.")
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


def runtime_command(program_path, runtime, use_valgrind=False):
    if runtime == "cpp":
        command = [f"./{EXECUTABLE}", program_path]
        if use_valgrind:
            return [*VALGRIND_ARGS, *command], True
        return command, False
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


def evaluate_program_fixture(
    test_name,
    fixture,
    program_path,
    runtime,
    use_valgrind=False,
    ignore_debug=False,
):
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

    command, under_valgrind = runtime_command(program_path, runtime, use_valgrind)
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

    ignored_fields = frozenset({"debug"}) if ignore_debug else frozenset()
    relaxed_expected_output = relax_expected_error_positions(expected_output)
    if expected_output_matches(relaxed_expected_output, actual_output, ignored_fields):
        return test_result(test_name, True)

    failure_lines.append("output mismatch")
    failure_lines.append("diff:")
    failure_lines.append(indent_block(render_yaml_diff(
        comparable_output(relaxed_expected_output, ignored_fields),
        comparable_output(actual_output, ignored_fields),
    )))
    return test_result(test_name, False, failure_lines)


def discover_yaml_fixtures(test_directory):
    fixture_paths = []
    for root, directories, files in os.walk(test_directory):
        directories[:] = [
            directory
            for directory in directories
            if directory != FIXTURE_ASSET_DIRECTORY
        ]
        fixture_paths.extend(
            os.path.join(root, file_name)
            for file_name in files
            if file_name.endswith((".yaml", ".yml"))
        )
    return sorted(fixture_paths)


def run_fixture_tests(test_directory, run_test_case, fail_fast=False):
    if not os.path.isdir(test_directory):
        print(f"No '{test_directory}' directory found.")
        return 0

    test_paths = discover_yaml_fixtures(test_directory)
    if not test_paths:
        print(f"No YAML test fixtures found in '{test_directory}'.")
        return 0

    results = []
    if fail_fast:
        for test_path in test_paths:
            result = run_test_case(test_path)
            results.append(result)
            if not result["passed"]:
                break
    else:
        max_workers = min(len(test_paths), os.cpu_count() or 1)
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


def run_tests(runtime="cpp", use_valgrind=False, fail_fast=False, ignore_debug=False):
    """Discovers and runs YAML fixtures in the 'tests' directory."""
    if use_valgrind and not command_exists("valgrind"):
        print("valgrind not found; install valgrind or run without --valgrind.")
        return 1

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
            return evaluate_program_fixture(
                test_name,
                fixture,
                program_path,
                runtime,
                use_valgrind,
                ignore_debug,
            )
        finally:
            if should_delete_program:
                os.unlink(program_path)

    return run_fixture_tests("tests", run_test_case, fail_fast)


def run_hcc_tests(runtime="cpp", use_valgrind=False, fail_fast=False, ignore_debug=False):
    """Discovers and runs C-to-Helix fixtures in the 'hcc/tests' directory."""
    if use_valgrind and not command_exists("valgrind"):
        print("valgrind not found; install valgrind or run without --valgrind.")
        return 1

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

            runtime_fixture = fixture
            if "expected" in fixture and "expected_state" in fixture:
                expected_program = fixture["expected"]
                if program != expected_program:
                    return test_result(
                        test_name,
                        False,
                        [
                            "emitted Helix mismatch",
                            "diff:",
                            indent_block(render_yaml_diff(expected_program, program)),
                        ],
                    )

                expected = dict(expected_program)
                expected["state"] = fixture["expected_state"]
                runtime_fixture = dict(fixture)
                runtime_fixture["expected"] = expected
            elif "expected_state" in fixture:
                expected = dict(program)
                expected["state"] = fixture["expected_state"]
                runtime_fixture = dict(fixture)
                runtime_fixture["expected"] = expected

            return evaluate_program_fixture(
                test_name,
                runtime_fixture,
                program_path,
                runtime,
                use_valgrind,
                ignore_debug,
            )
        except Exception as exc:
            return test_result(test_name, False, [f"hcc compile failed: {exc}"])
        finally:
            os.unlink(source_path)
            if program_path is not None:
                os.unlink(program_path)

    return run_fixture_tests("hcc/tests", run_test_case, fail_fast)


def run_hui_tests(fail_fast=False):
    """Runs deterministic scripted tests for the Python literal-YAML HUI demo."""
    suite = unittest.defaultTestLoader.discover("tui/tests")
    result = unittest.TextTestRunner(verbosity=2, failfast=fail_fast).run(suite)
    return len(result.failures) + len(result.errors)


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


def print_usage(script_name="manage.py"):
    print("usage:")
    print(f"  python3 {script_name} [--lib] [--no-dynamic-libraries] [--no-cpp-linenums] [--optimize-size] [--valgrind] [--fail-fast]")
    print("                                             # compile C++, optional libs, tidy, cloc, and run C++ fixtures")
    print(f"  python3 {script_name} build [--lib] [--no-dynamic-libraries] [--no-cpp-linenums] [--optimize-size]")
    print("                                             # compile the C++ scaffold and optional libs only")
    print(f"  python3 {script_name} cpp-test [--lib] [--no-dynamic-libraries] [--no-cpp-linenums] [--optimize-size] [--valgrind] [--fail-fast]")
    print("                                             # compile C++, optional libs, and run native fixtures")
    print(f"  python3 {script_name} hcc-test [--lib] [--no-dynamic-libraries] [--no-cpp-linenums] [--optimize-size] [--valgrind] [--fail-fast]")
    print("                                             # compile C++, optional libs, and run HCC fixtures")
    print(f"  python3 {script_name} hui-test [--fail-fast]")
    print("                                             # run deterministic Python HUI demo tests")
    print(f"  python3 {script_name} hui-demo [target.yaml] [--binary path]")
    print("                                             # launch the interactive literal-YAML HUI demo")


def main(arguments=None, default_command="default", script_name="manage.py"):
    arguments = sys.argv[1:] if arguments is None else arguments
    compile_libs = False
    dynamic_libraries = True
    cpp_linenums = True
    optimize_size = False
    use_valgrind = False
    fail_fast = False
    filtered_arguments = []

    for argument in arguments:
        if argument == "--lib":
            compile_libs = True
            continue
        if argument == "--no-dynamic-libraries":
            dynamic_libraries = False
            continue
        if argument == "--no-cpp-linenums":
            cpp_linenums = False
            continue
        if argument == "--optimize-size":
            optimize_size = True
            continue
        if argument == "--valgrind":
            use_valgrind = True
            continue
        if argument == "--fail-fast":
            fail_fast = True
            continue
        filtered_arguments.append(argument)

    command = filtered_arguments[0] if filtered_arguments else default_command

    dynamic_libraries, cpp_linenums = production_build_features(
        dynamic_libraries,
        cpp_linenums,
        optimize_size,
    )

    if command == "hui-demo":
        if compile_libs or use_valgrind or not dynamic_libraries or not cpp_linenums or optimize_size or fail_fast:
            print("hui-demo accepts only its target and --binary options.")
            sys.exit(1)
        from tui.hui_demo import main as run_hui_demo

        if run_hui_demo(filtered_arguments[1:]) != 0:
            sys.exit(1)
        return

    if len(filtered_arguments) > 1:
        print(f"unknown arguments: {' '.join(filtered_arguments[1:])}")
        print_usage(script_name)
        sys.exit(1)

    if command in {"-h", "--help", "help"}:
        print_usage(script_name)
        return

    if compile_libs and not dynamic_libraries:
        print("--lib requires dynamic library support.")
        sys.exit(1)

    if command == "build":
        if not compile_main(dynamic_libraries, optimize_size, cpp_linenums):
            sys.exit(1)
        if compile_libs and not compile_sfml_module(optimize_size, cpp_linenums):
            sys.exit(1)
        return

    if command in {"cpp-test", "test-cpp"}:
        if not compile_main(dynamic_libraries, optimize_size, cpp_linenums):
            sys.exit(1)
        if compile_libs and not compile_sfml_module(optimize_size, cpp_linenums):
            sys.exit(1)
        num_failed = run_tests("cpp", use_valgrind, fail_fast, not cpp_linenums)
        if num_failed > 0:
            sys.exit(1)
        return

    if command in {"hcc-test", "test-hcc"}:
        if not compile_main(dynamic_libraries, optimize_size, cpp_linenums):
            sys.exit(1)
        if compile_libs and not compile_sfml_module(optimize_size, cpp_linenums):
            sys.exit(1)
        num_failed = run_hcc_tests("cpp", use_valgrind, fail_fast, not cpp_linenums)
        if num_failed > 0:
            sys.exit(1)
        return

    if command in {"hui-test", "test-hui"}:
        if compile_libs or use_valgrind or not dynamic_libraries or not cpp_linenums or optimize_size:
            print("hui-test accepts only --fail-fast.")
            sys.exit(1)
        if run_hui_tests(fail_fast) > 0:
            sys.exit(1)
        return

    if command != "default":
        print(f"unknown command: {command}")
        print_usage(script_name)
        sys.exit(1)

    if not compile_main(dynamic_libraries, optimize_size, cpp_linenums):
        sys.exit(1)

    if compile_libs and not compile_sfml_module(optimize_size, cpp_linenums):
        sys.exit(1)

    if not run_clang_tidy():
        sys.exit(1)


    num_failed = run_tests("cpp", use_valgrind, fail_fast, not cpp_linenums)

    if not run_cloc():
        sys.exit(1)

    if not run_binary_size_report():
        sys.exit(1)
        
    if num_failed > 0:
        sys.exit(1)
