import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ruamel.yaml import YAML

from scripts import operations
from tui import helix_step, hui_demo


FIXTURE = Path(__file__).parents[2] / "tests" / "assets" / "hui_core_demo.yaml"
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class MemoryTerminal:
    def __init__(self, keys, rows=12, columns=72, raise_on_read=False):
        self.keys = iter(keys)
        self.rows = rows
        self.columns = columns
        self.raise_on_read = raise_on_read
        self.output = []
        self.entered = False
        self.restored = False

    def __enter__(self):
        self.entered = True
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.restored = True

    def read_key(self):
        if self.raise_on_read:
            raise RuntimeError("input failed")
        return next(self.keys)

    def write(self, text):
        self.output.append(text)

    def dimensions(self):
        return self.rows, self.columns


def document():
    return hui_demo.build_document(hui_demo.load_ordered_document(FIXTURE))


class RenderingTests(unittest.TestCase):
    def test_canonical_yaml_is_exact_after_ansi_is_removed(self):
        doc = document()
        styled = "".join(
            hui_demo.overlay_line(line, False, False) + "\n"
            for line in doc.lines
        )
        stripped = ANSI.sub("", styled).replace("\x1b", "")
        stripped = stripped.replace(hui_demo.BG, "")
        self.assertEqual(stripped, doc.text)

    def test_pc_and_selection_are_overlays_without_yaml_mutation(self):
        doc = document()
        before = hui_demo.canonical_yaml(doc.data)
        state = hui_demo.DemoState(selection=("workspace", "task", "steps", 1))
        rendered = hui_demo.render(doc, state, 40, 100)
        self.assertIn(hui_demo.PC_SELECTION_STYLE, rendered)
        self.assertEqual(hui_demo.canonical_yaml(doc.data), before)
        self.assertNotIn("<---", before)

    def test_yaml_syntax_colors_are_renderer_only(self):
        line = "answer: [true, 42, 'text'] # note"
        styled = hui_demo.overlay_line(line, False, False)
        self.assertIn(f"{hui_demo.YAML_KEY_STYLE}answer", styled)
        self.assertIn(f"{hui_demo.YAML_LITERAL_STYLE}true", styled)
        self.assertIn(f"{hui_demo.YAML_NUMBER_STYLE}42", styled)
        self.assertIn(f"{hui_demo.YAML_STRING_STYLE}'text'", styled)
        self.assertIn(f"{hui_demo.YAML_COMMENT_STYLE}# note", styled)
        self.assertEqual(ANSI.sub("", styled), line)

    def test_pc_and_selection_backgrounds_preserve_syntax_foregrounds(self):
        line = "status: running"
        styled = hui_demo.overlay_line(line, True, True)
        self.assertTrue(styled.startswith(hui_demo.PC_SELECTION_STYLE))
        self.assertIn(f"{hui_demo.YAML_KEY_STYLE}status", styled)
        self.assertIn(f"{hui_demo.YAML_STYLE}running", styled)

    def test_yaml_quotes_and_plain_hashes_do_not_start_comments(self):
        line = "values: [a#b, \"c:#d\", 'e:#f'] # note"
        styled = hui_demo.highlight_yaml(line)
        self.assertEqual(styled.count(hui_demo.YAML_COMMENT_STYLE), 1)
        self.assertIn(f"{hui_demo.YAML_STYLE}a#b", styled)
        self.assertIn(f"{hui_demo.YAML_STRING_STYLE}\"c:#d\"", styled)
        self.assertIn(f"{hui_demo.YAML_STRING_STYLE}'e:#f'", styled)
        self.assertEqual(ANSI.sub("", styled), line)

    def test_viewport_clips_to_fixed_dimensions(self):
        doc = document()
        state = hui_demo.DemoState(selection=("state", "frames", 0, 0))
        rendered = hui_demo.render(doc, state, 7, 31)
        visible = ANSI.sub("", rendered).splitlines()
        self.assertEqual(len(visible), 7)
        self.assertTrue(all(len(line) == 30 for line in visible))

    def test_render_preserves_page_scrolled_viewport(self):
        doc = document()
        state = hui_demo.DemoState(selection=(), viewport=5)
        rendered = ANSI.sub("", hui_demo.render(doc, state, 7, 72))
        self.assertIn("lines:6-10", rendered.splitlines()[0])

    def test_rows_reserve_last_column_and_do_not_double_space(self):
        doc = document()
        rendered = ANSI.sub("", hui_demo.render(doc, hui_demo.DemoState(()), 6, 20))
        visible = rendered.splitlines()
        self.assertEqual(len(visible), 6)
        self.assertTrue(all(len(line) == 19 for line in visible))


class ReducerTests(unittest.TestCase):
    def test_demo_is_a_coherent_nested_running_vm_chain(self):
        doc = document()
        self.assertEqual(doc.active_vm, ("workspace", "task"))
        self.assertEqual(
            hui_demo.pc_path(doc.data, doc.active_vm),
            ("workspace", "task", "steps", 1),
        )
        self.assertEqual(
            list(hui_demo.vm_ancestors(doc, doc.active_vm)),
            [("workspace", "task"), ("workspace",), ()],
        )
        self.assertEqual(
            doc.data["workspace"]["task"]["adjusted"],
            doc.data["workspace"]["task"]["sample"]
            + doc.data["workspace"]["offset"],
        )

    def test_run_arrows_drive_pc_and_left_right_traverse_semantics(self):
        doc = document()
        state = hui_demo.DemoState(selection=())
        state, _ = hui_demo.reduce_state(doc, state, hui_demo.RIGHT, 5)
        self.assertEqual(state.selection, ("keybinds",))
        unchanged, operation = hui_demo.reduce_state(doc, state, hui_demo.UP, 5)
        self.assertEqual(unchanged, state)
        self.assertEqual(operation, "step")
        unchanged, operation = hui_demo.reduce_state(doc, state, hui_demo.DOWN, 5)
        self.assertEqual(unchanged, state)
        self.assertEqual(operation, "back")
        state, _ = hui_demo.reduce_state(doc, state, hui_demo.RIGHT, 5)
        self.assertEqual(state.selection, ("keybinds", 0))
        state, _ = hui_demo.reduce_state(doc, state, hui_demo.LEFT, 5)
        self.assertEqual(state.selection, ("keybinds",))
        state, _ = hui_demo.reduce_state(doc, state, hui_demo.PAGE_DOWN, 5)
        self.assertEqual(state.viewport, 5)

    def test_f9_requests_one_backward_snapshot(self):
        state = hui_demo.DemoState(selection=())
        next_state, operation = hui_demo.reduce_state(
            document(),
            state,
            hui_demo.F9,
            5,
        )
        self.assertEqual(next_state, state)
        self.assertEqual(operation, "back")

    def test_escape_enters_write_mode_without_exiting(self):
        doc = document()
        state = hui_demo.DemoState(selection=("workspace", "task", "main"))
        state, operation = hui_demo.reduce_state(doc, state, hui_demo.ESCAPE, 8)
        self.assertIsNone(operation)
        self.assertEqual(state.mode, hui_demo.WRITE_MODE)
        self.assertFalse(state.exiting)
        self.assertEqual(state.editor.text(), doc.text)
        state, operation = hui_demo.reduce_state(doc, state, hui_demo.ESCAPE, 8)
        self.assertEqual(operation, "save")
        self.assertFalse(state.exiting)

    def test_appkeys_resolve_active_to_ancestor_nearest_first(self):
        doc = document()
        active = ("workspace", "task")
        self.assertEqual(hui_demo.resolve_appkey(doc, active, "T"), active)
        self.assertEqual(hui_demo.resolve_appkey(doc, active, "W"), ("workspace",))
        self.assertEqual(hui_demo.resolve_appkey(doc, active, "R"), ())

    def test_appkeys_exclude_siblings_and_unrelated_descendants(self):
        doc = document()
        self.assertIsNone(
            hui_demo.resolve_appkey(doc, ("workspace", "task"), "X")
        )

    def test_prototype_interrupt_switches_context_to_existing_pc(self):
        doc = document()
        state = hui_demo.DemoState(selection=("workspace", "task", "main"))
        state, operation = hui_demo.reduce_state(doc, state, "W", 20)
        self.assertIsNone(operation)
        self.assertEqual(state.context_vm, ("workspace",))
        self.assertEqual(state.selection, ("workspace", "actions", 1))
        state, _ = hui_demo.reduce_state(doc, state, "R", 20)
        self.assertEqual(state.context_vm, ())
        self.assertEqual(state.selection, ("actions", 1))


class EditorTests(unittest.TestCase):
    def test_text_editor_keys_insert_split_join_delete_and_move(self):
        editor = hui_demo.EditorBuffer(("abc", "de"), 0, 2, True)
        editor = hui_demo.edit_buffer(editor, "X")
        self.assertEqual((editor.lines, editor.cursor_column), (("abXc", "de"), 3))
        editor = hui_demo.edit_buffer(editor, hui_demo.ENTER_KEYS[0])
        self.assertEqual(editor.lines, ("abX", "c", "de"))
        editor = hui_demo.edit_buffer(editor, hui_demo.BACKSPACE)
        self.assertEqual(editor.lines, ("abXc", "de"))
        editor = hui_demo.edit_buffer(editor, hui_demo.DOWN)
        editor = hui_demo.edit_buffer(editor, hui_demo.HOME)
        editor = hui_demo.edit_buffer(editor, hui_demo.DELETE)
        self.assertEqual(editor.lines, ("abXc", "e"))
        editor = hui_demo.edit_buffer(editor, hui_demo.END)
        editor = hui_demo.edit_buffer(editor, hui_demo.RIGHT)
        self.assertEqual((editor.cursor_line, editor.cursor_column), (1, 1))

    def test_write_mode_suppresses_runtime_and_appkeys(self):
        calls = []

        def runtime(operation, binary_path, target_path):
            calls.append(operation)
            return hui_demo.load_ordered_document(FIXTURE)

        terminal = MemoryTerminal(
            [hui_demo.ESCAPE, "R", hui_demo.F5, hui_demo.UP, hui_demo.CTRL_Q]
        )
        hui_demo.run_demo(FIXTURE, Path("build/helix"), terminal, runtime)
        self.assertEqual(calls, [])
        self.assertIn(" WRITE ", ANSI.sub("", terminal.output[-1]))

    def test_valid_write_saves_exact_text_and_returns_to_run(self):
        source = "main: [add, 1, 2]\n"
        with tempfile.TemporaryDirectory(prefix="hui-edit-", dir="build") as directory:
            target = Path(directory) / "edit.yaml"
            target.write_text(source, encoding="utf-8")
            terminal = MemoryTerminal(
                [hui_demo.ESCAPE, hui_demo.END, " # note", hui_demo.ESCAPE, hui_demo.CTRL_Q]
            )
            hui_demo.run_demo(target, Path("build/helix"), terminal)
            self.assertEqual(
                target.read_text(encoding="utf-8"),
                "main: [add, 1, 2] # note\n",
            )
        self.assertIn(" RUN ", ANSI.sub("", terminal.output[-1]))

    def test_invalid_write_stays_buffered_and_does_not_touch_disk(self):
        source = "main: [add, 1, 2]\n"
        with tempfile.TemporaryDirectory(prefix="hui-edit-", dir="build") as directory:
            target = Path(directory) / "edit.yaml"
            target.write_text(source, encoding="utf-8")
            terminal = MemoryTerminal(
                [
                    hui_demo.ESCAPE,
                    hui_demo.LEFT,
                    hui_demo.LEFT,
                    hui_demo.DELETE,
                    hui_demo.ESCAPE,
                    hui_demo.CTRL_Q,
                ]
            )
            hui_demo.run_demo(target, Path("build/helix"), terminal)
            self.assertEqual(target.read_text(encoding="utf-8"), source)
        final_frame = ANSI.sub("", terminal.output[-1])
        self.assertIn(" WRITE ", final_frame)
        self.assertIn("YAML ERROR", final_frame)

class RuntimeAndTerminalTests(unittest.TestCase):
    def test_page_down_remains_visible_in_next_interactive_frame(self):
        terminal = MemoryTerminal([hui_demo.PAGE_DOWN, hui_demo.CTRL_Q], rows=7)
        hui_demo.run_demo(FIXTURE, Path("build/helix"), terminal)
        second_frame = ANSI.sub("", terminal.output[1])
        self.assertIn("lines:6-10", second_frame.splitlines()[0])

    def test_run_arrows_and_function_keys_delegate_and_reload_yaml(self):
        calls = []
        yaml = YAML(typ="safe")
        initial = yaml.load(FIXTURE.read_text(encoding="utf-8"))

        def runtime(operation, binary_path, target_path):
            calls.append((operation, binary_path, target_path))
            changed = dict(initial)
            changed["global-value"] = len(calls)
            return changed

        terminal = MemoryTerminal(
            [
                hui_demo.UP,
                hui_demo.DOWN,
                hui_demo.F10,
                hui_demo.F5,
                hui_demo.F9,
                hui_demo.CTRL_Q,
            ]
        )
        binary = Path("build/helix")
        hui_demo.run_demo(FIXTURE, binary, terminal, runtime)
        self.assertEqual(
            [call[0] for call in calls],
            ["step", "back", "step", "start", "back"],
        )
        self.assertTrue(terminal.restored)
        self.assertGreaterEqual(len(terminal.output), 6)

    def test_versioned_runtime_maps_operations_and_disables_pc_comments(self):
        restored = {"main": ["add", 1, 2]}
        operations = (
            ("step", hui_demo.STEP_FORWARD_OPERATION),
            ("start", hui_demo.CONTINUE_OPERATION),
            ("back", hui_demo.STEP_BACKWARD_OPERATION),
        )
        with (
            mock.patch(
                "tui.hui_demo.execute_debug_operation",
                return_value=True,
            ) as execute,
            mock.patch(
                "tui.hui_demo.load_ordered_document",
                return_value=restored,
            ) as reload_vm,
        ):
            for operation, expected_debug_operation in operations:
                self.assertIs(
                    hui_demo.execute_runtime_operation(
                        operation,
                        Path("build/helix"),
                        Path("build/debug_demo/demo.yaml"),
                    ),
                    restored,
                )
                execute.assert_called_with(
                    expected_debug_operation,
                    Path("build/helix"),
                    Path("build/debug_demo/demo.yaml"),
                    Path("build/debug_demo/demo.yaml"),
                    annotate_pc=False,
                )
        self.assertEqual(reload_vm.call_count, len(operations))

    def test_terminal_restores_after_backward_history_error(self):
        def runtime(operation, binary_path, target_path):
            raise RuntimeError("history failed")

        terminal = MemoryTerminal([hui_demo.F9])
        with self.assertRaisesRegex(RuntimeError, "history failed"):
            hui_demo.run_demo(
                FIXTURE,
                Path("build/helix"),
                terminal,
                runtime,
            )
        self.assertTrue(terminal.restored)

    def test_terminal_restores_after_input_error(self):
        terminal = MemoryTerminal([], raise_on_read=True)
        with self.assertRaisesRegex(RuntimeError, "input failed"):
            hui_demo.run_demo(FIXTURE, Path("build/helix"), terminal)
        self.assertTrue(terminal.entered)
        self.assertTrue(terminal.restored)


class HistoryAdapterTests(unittest.TestCase):
    def test_first_step_preserves_literal_yaml_structure(self):
        yaml = YAML(typ="safe")
        runtime_state = yaml.load(FIXTURE.read_text(encoding="utf-8"))
        runtime_state["workspace"]["task"]["doubled"] = 84

        with tempfile.TemporaryDirectory(
            prefix="hui-roundtrip-",
            dir="build",
        ) as directory:
            target = Path(directory) / "demo.yaml"
            shutil.copy2(FIXTURE, target)
            original_text = target.read_text(encoding="utf-8")
            with mock.patch(
                "tui.helix_step.run_helix",
                return_value={helix_step.WRAPPER_VM_NAME: runtime_state},
            ):
                helix_step.step_target_file(
                    Path(__file__),
                    target,
                    target,
                    "step",
                    annotate_pc=False,
                )
            stepped_text = target.read_text(encoding="utf-8")
            displayed_text = hui_demo.build_document(
                hui_demo.load_ordered_document(target)
            ).text

        self.assertIn('help: "Return to the root controller"', stepped_text)
        self.assertIn("release: 1 # final summary calibration", stepped_text)
        self.assertIn("keybinds: [R]", stepped_text)
        self.assertIn(
            "      - [set, doubled, [mul, adjusted, 2]]",
            stepped_text,
        )
        self.assertIn("    main: [run, steps]", stepped_text)
        self.assertNotIn("<---", stepped_text)
        self.assertEqual(displayed_text, stepped_text)
        self.assertEqual(
            stepped_text.replace("    doubled: 84\n", ""),
            original_text,
        )

    def test_forward_snapshot_disables_persisted_pc_annotation(self):
        repo = mock.Mock(head_is_detached=False)
        with (
            mock.patch("tui.helix_step.open_debug_repo", return_value=repo),
            mock.patch("tui.helix_step.step_target_file") as step,
            mock.patch("tui.helix_step.commit_debug_snapshot") as commit,
        ):
            helix_step.step_and_commit(
                Path("build/helix"),
                Path("build/debug_demo/demo.yaml"),
                Path("build/demo.yaml"),
                "step",
                annotate_pc=False,
            )
        step.assert_called_once_with(
            Path("build/helix"),
            Path("build/debug_demo/demo.yaml"),
            Path("build/demo.yaml"),
            "step",
            False,
        )
        commit.assert_called_once_with(
            Path("build/debug_demo"),
            "VM state",
        )

    def test_forward_reuses_unannotated_snapshot_after_backward(self):
        repo = mock.Mock(head_is_detached=True)
        snapshot = object()
        with (
            mock.patch("tui.helix_step.open_debug_repo", return_value=repo),
            mock.patch(
                "tui.helix_step.next_preserved_snapshot",
                return_value=snapshot,
            ),
            mock.patch(
                "tui.helix_step.preserved_snapshot_has_pc_comment",
            ) as has_comment,
            mock.patch("tui.helix_step.checkout_detached_commit") as checkout,
            mock.patch("tui.helix_step.step_target_file") as step,
        ):
            helix_step.step_and_commit(
                Path("build/helix"),
                Path("build/debug_demo/demo.yaml"),
                Path("build/demo.yaml"),
                "step",
                annotate_pc=False,
            )
        has_comment.assert_not_called()
        checkout.assert_called_once_with(repo, snapshot)
        step.assert_not_called()


class EntrypointTests(unittest.TestCase):
    def test_interactive_entrypoint_opens_versioned_working_copy(self):
        target = Path("build/hui_demo.yaml")
        debug_target = Path("build/debug_hui_demo/hui_demo.yaml")
        binary = Path("build/helix")
        terminal = object()
        with (
            mock.patch("tui.hui_demo.prepare_target_path", return_value=target),
            mock.patch(
                "tui.hui_demo.ensure_debug_repo",
                return_value=debug_target,
            ) as ensure,
            mock.patch("tui.hui_demo.resolve_binary_path", return_value=binary),
            mock.patch("tui.hui_demo.PosixTerminal", return_value=terminal),
            mock.patch("tui.hui_demo.run_demo") as run,
        ):
            self.assertEqual(hui_demo.main([]), 0)
        ensure.assert_called_once_with(target)
        run.assert_called_once()
        debug_path, resolved_binary, selected_terminal, runtime = run.call_args.args
        self.assertEqual(
            (debug_path, resolved_binary, selected_terminal),
            (debug_target, binary, terminal),
        )
        self.assertIs(runtime.func, hui_demo.execute_runtime_operation)
        self.assertEqual(runtime.keywords, {"include_source_path": target})

    def test_direct_script_help_resolves_project_imports(self):
        completed = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).parents[1] / "hui_demo.py"),
                "--help",
            ],
            cwd=Path(__file__).parents[2],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Prototype literal-YAML Helix terminal interface", completed.stdout)

    def test_root_workflow_routes_demo_arguments(self):
        with mock.patch("tui.hui_demo.main", return_value=0) as run_demo:
            operations.main(
                ["hui-demo", "example.yaml", "--binary", "custom-helix"],
                script_name="run.py",
            )
        run_demo.assert_called_once_with(
            ["example.yaml", "--binary", "custom-helix"]
        )

    def test_default_demo_uses_a_fresh_generated_working_copy(self):
        with (
            mock.patch("tui.hui_demo.Path.mkdir") as mkdir,
            mock.patch("tui.hui_demo.shutil.copy2") as copy2,
        ):
            target = hui_demo.prepare_target_path(None)
        self.assertEqual(target, Path(__file__).parents[2] / "build" / "hui_demo.yaml")
        mkdir.assert_called_once_with(exist_ok=True)
        copy2.assert_called_once_with(FIXTURE, target)


if __name__ == "__main__":
    unittest.main()
