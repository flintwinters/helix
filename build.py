import difflib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

from rich import print

import yaml

INCLUDE_DIRECTORIES = ["include", "src", "ryml/src", "ryml/ext/c4core/src"]
INCLUDES = " ".join(f"-I{directory}" for directory in INCLUDE_DIRECTORIES)
COMPILER = "g++"
CPP_FLAGS = "-g -std=c++20"
LINKER_FLAGS = "-rdynamic -L ryml/build -lryml -ldl"
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


def is_source_newer(source_path, output_path):
    return not os.path.exists(output_path) or os.path.getmtime(source_path) > os.path.getmtime(output_path)


INCLUDE_PATTERN = re.compile(r'^\s*#\s*include\s*[<"]([^">]+)[">]')


def included_project_files(source_path, include_directories):
    resolved_paths = set()
    pending_paths = [source_path]
    visited_paths = set()

    while pending_paths:
        current_path = pending_paths.pop()
        if current_path in visited_paths or not os.path.exists(current_path):
            continue

        visited_paths.add(current_path)

        with open(current_path, "r", encoding="utf-8") as source_file:
            for line in source_file:
                match = INCLUDE_PATTERN.match(line)
                if not match:
                    continue

                include_name = match.group(1)
                candidate_paths = [os.path.join(os.path.dirname(current_path), include_name)]
                candidate_paths.extend(os.path.join(directory, include_name) for directory in include_directories)

                for candidate_path in candidate_paths:
                    normalized_path = os.path.normpath(candidate_path)
                    if not os.path.exists(normalized_path):
                        continue

                    if normalized_path.startswith("src") or normalized_path.startswith("include"):
                        if normalized_path not in resolved_paths:
                            resolved_paths.add(normalized_path)
                            pending_paths.append(normalized_path)
                    break

    return resolved_paths


def newest_dependency_mtime(source_path, include_directories):
    dependency_paths = {source_path}
    dependency_paths.update(included_project_files(source_path, include_directories))
    return max(os.path.getmtime(path) for path in dependency_paths)


def should_recompile(source_path, object_path, include_directories):
    if not os.path.exists(object_path):
        return True

    return newest_dependency_mtime(source_path, include_directories) > os.path.getmtime(object_path)


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

        if not should_recompile(source_path, object_path, INCLUDE_DIRECTORIES):
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
        executable_mtime = os.path.getmtime(EXECUTABLE)
        should_link = os.path.getmtime(__file__) > executable_mtime or any(
            os.path.getmtime(object_path) > executable_mtime for object_path in object_files
        )
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


def compile_sfml_module():
    """Compiles the SFML wrapper into a native include module."""
    os.makedirs(os.path.dirname(SFML_MODULE), exist_ok=True)

    should_compile = (
        not os.path.exists(SFML_MODULE)
        or newest_dependency_mtime(SFML_MODULE_SOURCE, INCLUDE_DIRECTORIES) > os.path.getmtime(SFML_MODULE)
        or os.path.getmtime(__file__) > os.path.getmtime(SFML_MODULE)
    )
    if not should_compile:
        print("SFML module compilation successful.")
        return True

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


def run_tests(runtime="cpp"):
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

        if not isinstance(fixture, dict):
            return {
                "name": test_name,
                "passed": False,
                "failure_lines": ["fixture must be a YAML mapping"],
            }

        has_program = "program" in fixture
        has_source = "source" in fixture
        if has_program == has_source:
            return {
                "name": test_name,
                "passed": False,
                "failure_lines": ["fixture must contain exactly one of 'program' or 'source'"],
            }

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
            command, under_valgrind = runtime_command(program_path, runtime)
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
            )
        finally:
            if should_delete_program:
                os.unlink(program_path)

        allowed_return_codes = (0, 101) if under_valgrind else (0,)
        if result.returncode not in allowed_return_codes:
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

        if under_valgrind and result.returncode == 101:
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
        failure_lines.append("diff:")
        failure_lines.append(indent_block(render_yaml_diff(expected_output, actual_output)))
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
            print(f"[bold red]([/bold red]{result['name']}[bold red]) -> FAILED[/bold red]")
            for line in result["failure_lines"]:
                print(f"  {line}")

    print(f"Passed: {passed}, Failed: {len(failed_results)}, Total: {len(results)}")
    return len(failed_results)


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


def print_usage():
    print("usage:")
    print("  python3 build.py [--lib]                # compile C++, optional libs, tidy, cloc, and run C++ fixtures")
    print("  python3 build.py build [--lib]          # compile the C++ scaffold and optional libs only")
    print("  python3 build.py racket-build   # precompile the current Racket runtime")
    print("  python3 build.py cpp-test [--lib]       # compile C++, optional libs, and run native fixtures")
    print("  python3 build.py racket-test    # run fixtures against the current Racket runtime")


def main():
    arguments = sys.argv[1:]
    compile_libs = False
    filtered_arguments = []

    for argument in arguments:
        if argument == "--lib":
            compile_libs = True
            continue
        filtered_arguments.append(argument)

    if len(filtered_arguments) > 1:
        print(f"unknown arguments: {' '.join(filtered_arguments[1:])}")
        print_usage()
        sys.exit(1)

    command = filtered_arguments[0] if filtered_arguments else "default"

    if command in {"-h", "--help", "help"}:
        print_usage()
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

    if command != "default":
        print(f"unknown command: {command}")
        print_usage()
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

        
if __name__ == "__main__":
    main()
