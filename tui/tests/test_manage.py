import unittest
from unittest import mock

from typer.testing import CliRunner

import manage
from scripts import operations


class ManageWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def test_no_command_routes_complete_default_workflow(self):
        with mock.patch("manage.dispatch") as dispatch:
            result = self.runner.invoke(manage.app, [])
        self.assertEqual(result.exit_code, 0, result.output)
        dispatch.assert_called_once_with([])

    def test_hui_demo_routes_target_and_binary(self):
        with mock.patch("manage.dispatch") as dispatch:
            result = self.runner.invoke(
                manage.app,
                ["hui-demo", "example.yaml", "--binary", "custom-helix"],
            )
        self.assertEqual(result.exit_code, 0, result.output)
        dispatch.assert_called_once_with(
            ["hui-demo", "example.yaml", "--binary", "custom-helix"]
        )

    def test_test_commands_route_supported_options(self):
        cases = (
            (["hui-test", "--fail-fast"], ["hui-test", "--fail-fast"]),
            (
                ["cpp-test", "--fail-fast", "--valgrind"],
                ["cpp-test", "--fail-fast", "--valgrind"],
            ),
            (
                ["cpp-test", "--optimize-size"],
                ["cpp-test", "--optimize-size"],
            ),
            (["hcc-test", "--valgrind"], ["hcc-test", "--valgrind"]),
        )
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                with mock.patch("manage.dispatch") as dispatch:
                    result = self.runner.invoke(manage.app, arguments)
                self.assertEqual(result.exit_code, 0, result.output)
                dispatch.assert_called_once_with(expected)


class SizeOptimizedBuildTests(unittest.TestCase):
    def test_size_profile_disables_metadata_and_dynamic_exports(self):
        compile_flags = operations.compile_cpp_flags(
            dynamic_libraries=True,
            optimize_size=True,
            cpp_linenums=True,
        )
        linker_flags = operations.compile_linker_flags(
            dynamic_libraries=True,
            optimize_size=True,
        )

        self.assertIn("-Os", compile_flags)
        self.assertIn("-flto", compile_flags)
        self.assertIn("-fno-rtti", compile_flags)
        self.assertIn("-DHELIX_ENABLE_DYNAMIC_LIBRARIES=0", compile_flags)
        self.assertIn("-DHELIX_ENABLE_CPP_LINENUMS=0", compile_flags)
        self.assertIn("-flto", linker_flags)
        self.assertIn("-s", linker_flags.split())
        self.assertNotIn("-rdynamic", linker_flags)
        self.assertNotIn("-ldl", linker_flags)

    def test_size_profile_passes_production_features_to_compiler(self):
        with mock.patch("scripts.operations.compile_main", return_value=True) as compile_main:
            operations.main(["build", "--optimize-size"])

        compile_main.assert_called_once_with(False, True, False)

    def test_size_profile_rejects_optional_shared_modules(self):
        with self.assertRaises(SystemExit):
            operations.main(["build", "--optimize-size", "--lib"])

    def test_size_profile_builds_ryml_with_matching_release_features(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch("scripts.operations.subprocess.run", return_value=completed) as run:
            self.assertTrue(operations.configure_ryml(optimize_size=True))

        configure, build = (call.args[0] for call in run.call_args_list)
        self.assertIn("-DCMAKE_BUILD_TYPE=MinSizeRel", configure)
        self.assertIn("-DCMAKE_INTERPROCEDURAL_OPTIMIZATION=ON", configure)
        self.assertIn("-DRYML_SHORT_ERR_MSG=ON", configure)
        self.assertIn(
            "-DCMAKE_CXX_FLAGS_MINSIZEREL=-Os -DNDEBUG -flto -fno-rtti",
            configure,
        )
        self.assertEqual(
            build,
            [
                "cmake",
                "--build",
                "build/dependencies/ryml-min-size",
                "--config",
                "MinSizeRel",
            ],
        )

    def test_normal_profile_uses_isolated_ryml_release_configuration(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch("scripts.operations.subprocess.run", return_value=completed) as run:
            self.assertTrue(operations.configure_ryml(optimize_size=False))

        configure = run.call_args_list[0].args[0]
        self.assertIn("-DCMAKE_BUILD_TYPE=Release", configure)
        self.assertIn("-DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF", configure)
        self.assertIn("-DRYML_SHORT_ERR_MSG=OFF", configure)
        self.assertIn("-DCMAKE_CXX_FLAGS_RELEASE=-O3 -DNDEBUG", configure)
        self.assertIn("build/dependencies/ryml-release", configure)


if __name__ == "__main__":
    unittest.main()
