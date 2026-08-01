import unittest
from unittest import mock

from typer.testing import CliRunner

import manage


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
            (["hcc-test", "--valgrind"], ["hcc-test", "--valgrind"]),
        )
        for arguments, expected in cases:
            with self.subTest(arguments=arguments):
                with mock.patch("manage.dispatch") as dispatch:
                    result = self.runner.invoke(manage.app, arguments)
                self.assertEqual(result.exit_code, 0, result.output)
                dispatch.assert_called_once_with(expected)


if __name__ == "__main__":
    unittest.main()
