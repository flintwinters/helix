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
        self.assertEqual(stripped, doc.text)

    def test_pc_and_selection_are_overlays_without_yaml_mutation(self):
        doc = document()
        before = hui_demo.canonical_yaml(doc.data)
        state = hui_demo.DemoState(selection=("workspace", "task", "steps", 0))
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
        appkey_line = "keybinds: [K, R]"
        appkey_styled = hui_demo.overlay_line(
            appkey_line,
            False,
            False,
            ((11, 12),),
        )
        self.assertIn(f"{hui_demo.APPKEY_STYLE}{hui_demo.YAML_STYLE}K", appkey_styled)
        self.assertEqual(ANSI.sub("", appkey_styled), appkey_line)

    def test_pc_and_selection_styles_preserve_syntax_foregrounds(self):
        line = "status: running"
        styled = hui_demo.overlay_line(line, True, True)
        self.assertTrue(styled.startswith(hui_demo.PC_SELECTION_STYLE))
        self.assertIn(f"{hui_demo.YAML_KEY_STYLE}status", styled)
        self.assertIn(f"{hui_demo.YAML_STYLE}running", styled)

    def test_renderer_uses_background_only_for_selected_row(self):
        rendered = hui_demo.render(
            document(),
            hui_demo.DemoState(("keybinds",)),
            12,
            72,
        )
        background_styles = []
        for match in re.finditer(r"\x1b\[([0-9;]*)m", rendered):
            parameters = {
                int(parameter)
                for parameter in match.group(1).split(";")
                if parameter
            }
            self.assertNotIn(7, parameters, match.group(0))
            if 48 in parameters or not parameters.isdisjoint(
                {*range(40, 50), *range(100, 108)}
            ):
                background_styles.append(match.group(0))
        self.assertEqual(background_styles, [hui_demo.SELECTION_STYLE])

    def test_run_mode_highlights_only_currently_active_keybind_scalars(self):
        doc = document()
        state = hui_demo.DemoState(("workspace", "task", "steps", 1))
        rendered_lines = hui_demo.render(doc, state, 60, 100).split("\r\n")
        active_spans = hui_demo.active_appkey_spans(
            doc,
            ("workspace", "task"),
        )
        active_lines = {declaration.line for declaration in active_spans}
        expected_lines = {
            doc.node_by_path[("keybinds",)].line,
            doc.node_by_path[("workspace", "keybinds")].line,
            doc.node_by_path[("workspace", "task", "keybinds")].line,
        }
        self.assertEqual(active_lines, expected_lines)
        for line_index in expected_lines:
            self.assertIn(hui_demo.APPKEY_STYLE, rendered_lines[line_index + 1])
        sibling_line = doc.node_by_path[("unrelated-worker", "keybinds")].line
        self.assertNotIn(hui_demo.APPKEY_STYLE, rendered_lines[sibling_line + 1])
        root_declaration = next(
            declaration
            for declaration in active_spans
            if declaration.vm_path == ()
        )
        root_line = doc.lines[root_declaration.line]
        self.assertEqual(
            root_line[root_declaration.start : root_declaration.end],
            "R",
        )
        workspace_keys = {
            declaration.key
            for declaration in hui_demo.active_appkey_spans(doc, ("workspace",))
        }
        self.assertEqual(workspace_keys, {"W", "R"})

    def test_write_mode_removes_active_keybind_highlights(self):
        doc = document()
        run_state = hui_demo.DemoState(("workspace", "task", "main"))
        write_state, _ = hui_demo.reduce_state(
            doc,
            run_state,
            hui_demo.ESCAPE,
            58,
        )
        rendered = hui_demo.render(doc, write_state, 60, 100)
        self.assertNotIn(hui_demo.APPKEY_STYLE, rendered)

    def test_shadowed_ancestor_keybind_is_not_highlighted(self):
        doc = hui_demo.build_document(
            {
                "keybinds": ["K"],
                "child": {
                    "keybinds": ["K"],
                    "main": ["add", 1, 2],
                    "state": {
                        "status": "running",
                        "frames": [["main"]],
                    },
                },
                "main": ["step", "child"],
            }
        )
        active = hui_demo.active_appkey_spans(doc, ("child",))
        self.assertEqual(
            [(declaration.key, declaration.vm_path) for declaration in active],
            [("K", ("child",))],
        )

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
            ("workspace", "task", "steps", 0),
        )
        self.assertEqual(
            list(hui_demo.vm_ancestors(doc, doc.active_vm)),
            [("workspace", "task"), ("workspace",), ()],
        )
        self.assertNotIn("adjusted", doc.data["workspace"]["task"])

    def test_run_arrows_drive_pc_and_left_right_traverse_semantics(self):
        doc = document()
        state = hui_demo.DemoState(selection=())
        state, _ = hui_demo.reduce_state(doc, state, hui_demo.RIGHT, 5)
        self.assertEqual(state.selection, ("keybinds",))
        unchanged, operation = hui_demo.reduce_state(doc, state, hui_demo.UP, 5)
        self.assertEqual(unchanged, state)
        self.assertEqual(operation, "back")
        unchanged, operation = hui_demo.reduce_state(doc, state, hui_demo.DOWN, 5)
        self.assertEqual(unchanged, state)
        self.assertEqual(operation, "step")
        state, _ = hui_demo.reduce_state(doc, state, hui_demo.RIGHT, 5)
        self.assertEqual(state.selection, ("keybinds", 0))
        state, _ = hui_demo.reduce_state(doc, state, hui_demo.LEFT, 5)
        self.assertEqual(state.selection, ("keybinds",))
        state, _ = hui_demo.reduce_state(doc, state, hui_demo.PAGE_DOWN, 5)
        self.assertEqual(state.viewport, 5)

    def test_f9_requests_one_backward_snapshot(self):
        state = hui_demo.DemoState(selection=(), context_vm=("workspace",))
        next_state, operation = hui_demo.reduce_state(
            document(),
            state,
            hui_demo.F9,
            5,
        )
        self.assertIsNone(next_state.context_vm)
        self.assertEqual(operation, "back")

    def test_finished_program_suppresses_forward_controls_but_not_back(self):
        data = hui_demo.load_ordered_document(FIXTURE)
        data["state"]["status"] = "finished"
        data["state"]["frames"] = []
        doc = hui_demo.build_document(data)
        state = hui_demo.DemoState(selection=("main",))

        for key in (hui_demo.DOWN, hui_demo.F10, hui_demo.F5):
            next_state, operation = hui_demo.reduce_state(doc, state, key, 20)
            self.assertEqual(next_state, state)
            self.assertIsNone(operation)

        _, operation = hui_demo.reduce_state(doc, state, hui_demo.UP, 20)
        self.assertEqual(operation, "back")

    def test_back_after_appkey_returns_to_versioned_yaml_pc(self):
        doc = document()
        state = hui_demo.DemoState(selection=doc.active_vm)
        state, _ = hui_demo.reduce_state(doc, state, "W", 20)
        self.assertEqual(state.context_vm, ("workspace",))

        state, operation = hui_demo.reduce_state(doc, state, hui_demo.F9, 20)
        self.assertEqual(operation, "back")
        self.assertIsNone(state.context_vm)

        state = hui_demo.reconcile_document_state(doc, state)
        self.assertEqual(state.selection, ("workspace", "task", "steps", 0))

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
            ["back", "step", "step", "start", "back"],
        )
        self.assertTrue(terminal.restored)
        self.assertGreaterEqual(len(terminal.output), 6)

    def test_forward_operations_use_runtime_while_back_is_external(self):
        restored = {"main": ["add", 1, 2]}
        operations = (
            ("step", hui_demo.STEP_FORWARD_OPERATION),
            ("start", hui_demo.CONTINUE_OPERATION),
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
            mock.patch(
                "tui.hui_demo.restore_previous_yaml_snapshot",
            ) as restore,
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
            self.assertIs(
                hui_demo.execute_runtime_operation(
                    "back",
                    Path("missing-helix-binary"),
                    Path("build/debug_demo/demo.yaml"),
                ),
                restored,
            )
        restore.assert_called_once_with(Path("build/debug_demo/demo.yaml"))
        self.assertEqual(execute.call_count, len(operations))
        self.assertEqual(reload_vm.call_count, len(operations) + 1)

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
    def test_legacy_debugger_arrows_match_down_forward_up_external_back(self):
        target = Path("build/debug_demo/demo.yaml")
        with (
            mock.patch("tui.helix_step.step_and_commit") as step,
            mock.patch("tui.helix_step.restore_previous_yaml_snapshot") as back,
        ):
            handlers = helix_step.operation_handlers(
                Path("build/helix"),
                target,
                Path("build/demo.yaml"),
                annotate_pc=False,
            )
            handlers[helix_step.DOWN_ARROW]()
            handlers[helix_step.UP_ARROW]()
        step.assert_called_once_with(
            Path("build/helix"),
            target,
            Path("build/demo.yaml"),
            "step",
            False,
        )
        back.assert_called_once_with(target)

    def test_external_back_versions_dirty_yaml_before_checkout(self):
        target = Path("build/debug_demo/demo.yaml")
        with (
            mock.patch(
                "tui.helix_step.debug_target_has_uncommitted_changes",
                return_value=True,
            ) as changed,
            mock.patch("tui.helix_step.commit_manual_edit_to_fork") as preserve,
            mock.patch("tui.helix_step.checkout_previous_snapshot") as checkout,
        ):
            helix_step.restore_previous_yaml_snapshot(target)
        changed.assert_called_once_with(target)
        preserve.assert_called_once_with(target)
        checkout.assert_called_once_with(target)

    def test_yaml_snapshots_stage_only_the_debug_target(self):
        repo = mock.Mock()
        repo.head_is_unborn = True
        pygit2 = mock.Mock()
        pygit2.Signature.return_value = object()
        with (
            mock.patch("tui.helix_step.load_pygit2", return_value=pygit2),
            mock.patch("tui.helix_step.open_debug_repo", return_value=repo),
        ):
            helix_step.commit_debug_snapshot(
                Path("build/debug_demo"),
                "VM state",
                "demo.yaml",
            )
        repo.index.add.assert_called_once_with("demo.yaml")
        repo.index.add_all.assert_not_called()

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
            mock.patch(
                "tui.helix_step.load_target_vm",
                return_value={"main": ["add", 1, 2]},
            ),
            mock.patch("tui.helix_step.open_debug_repo", return_value=repo),
            mock.patch("tui.helix_step.step_target_file") as step,
            mock.patch(
                "tui.helix_step.debug_target_has_uncommitted_changes",
                return_value=True,
            ),
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
            "demo.yaml",
        )

    def test_continue_targets_deepest_running_vm_run_block(self):
        target_vm = helix_step.load_target_vm(FIXTURE)

        wrapper = helix_step.build_wrapper_vm(target_vm, "start")

        self.assertEqual(
            wrapper["main"],
            ["start", "__debug_target__.workspace.task"],
        )

    def test_step_still_targets_root_vm(self):
        target_vm = helix_step.load_target_vm(FIXTURE)

        wrapper = helix_step.build_wrapper_vm(target_vm, "step")

        self.assertEqual(wrapper["main"], ["step", "__debug_target__"])

    def test_continue_rejects_ambiguous_running_vm_siblings(self):
        target_vm = {
            "left": {"main": ["add", 1, 1], "state": {"status": "running"}},
            "right": {"main": ["add", 2, 2], "state": {"status": "running"}},
            "main": ["add", 0, 0],
            "state": {"status": "running"},
        }

        with self.assertRaisesRegex(ValueError, "unambiguous running VM ancestry"):
            helix_step.build_wrapper_vm(target_vm, "start")

    def test_textually_null_step_does_not_create_snapshot(self):
        repo = mock.Mock(head_is_detached=False)
        with (
            mock.patch(
                "tui.helix_step.load_target_vm",
                return_value={"main": ["add", 1, 2]},
            ),
            mock.patch("tui.helix_step.open_debug_repo", return_value=repo),
            mock.patch("tui.helix_step.step_target_file") as step,
            mock.patch(
                "tui.helix_step.debug_target_has_uncommitted_changes",
                return_value=False,
            ) as changed,
            mock.patch("tui.helix_step.commit_debug_snapshot") as commit,
        ):
            helix_step.step_and_commit(
                Path("build/helix"),
                Path("build/debug_demo/demo.yaml"),
                Path("build/demo.yaml"),
                "step",
                annotate_pc=False,
            )
        step.assert_called_once()
        changed.assert_called_once_with(Path("build/debug_demo/demo.yaml"))
        commit.assert_not_called()

    def test_finished_program_never_opens_history_or_invokes_helix(self):
        finished_vm = {
            "main": ["add", 1, 2],
            "state": {"status": "finished", "frames": [], "result": 3},
        }
        with (
            mock.patch(
                "tui.helix_step.load_target_vm",
                return_value=finished_vm,
            ) as load,
            mock.patch("tui.helix_step.open_debug_repo") as open_repo,
            mock.patch("tui.helix_step.step_target_file") as step,
            mock.patch("tui.helix_step.commit_debug_snapshot") as commit,
        ):
            helix_step.step_and_commit(
                Path("missing-helix-binary"),
                Path("build/debug_demo/demo.yaml"),
                Path("build/demo.yaml"),
                "step",
                annotate_pc=False,
            )

        load.assert_called_once()
        open_repo.assert_not_called()
        step.assert_not_called()
        commit.assert_not_called()

    def test_detached_null_step_does_not_create_history_branch(self):
        repo = mock.Mock(head_is_detached=True)
        with (
            mock.patch(
                "tui.helix_step.load_target_vm",
                return_value={"main": ["add", 1, 2]},
            ),
            mock.patch("tui.helix_step.open_debug_repo", return_value=repo),
            mock.patch(
                "tui.helix_step.next_preserved_snapshot",
                return_value=None,
            ),
            mock.patch("tui.helix_step.step_target_file") as step,
            mock.patch(
                "tui.helix_step.debug_target_has_uncommitted_changes",
                return_value=False,
            ),
            mock.patch("tui.helix_step.checkout_detached_commit") as checkout,
            mock.patch("tui.helix_step.commit_debug_snapshot") as commit,
        ):
            helix_step.step_and_commit(
                Path("build/helix"),
                Path("build/debug_demo/demo.yaml"),
                Path("build/demo.yaml"),
                "step",
                annotate_pc=False,
            )
        step.assert_called_once()
        checkout.assert_not_called()
        commit.assert_not_called()

    def test_forward_reuses_unannotated_snapshot_after_backward(self):
        repo = mock.Mock(head_is_detached=True)
        snapshot = object()
        with (
            mock.patch(
                "tui.helix_step.load_target_vm",
                return_value={"main": ["add", 1, 2]},
            ),
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
                script_name="manage.py",
            )
        run_demo.assert_called_once_with(
            ["example.yaml", "--binary", "custom-helix"]
        )

    def test_default_demo_uses_a_fresh_generated_working_copy(self):
        with (
            mock.patch("tui.hui_demo.Path.mkdir") as mkdir,
            mock.patch("tui.hui_demo.tempfile.NamedTemporaryFile") as temporary,
            mock.patch("tui.hui_demo.shutil.copy2") as copy2,
        ):
            temporary.return_value.__enter__.return_value.name = (
                Path(__file__).parents[2] / "build" / "hui_demo_unique.yaml"
            )
            target = hui_demo.prepare_target_path(None)
        self.assertEqual(
            target,
            Path(__file__).parents[2] / "build" / "hui_demo_unique.yaml",
        )
        mkdir.assert_called_once_with(exist_ok=True)
        temporary.assert_called_once_with(
            dir=Path(__file__).parents[2] / "build",
            prefix="hui_demo_",
            suffix=".yaml",
            delete=False,
        )
        copy2.assert_called_once_with(FIXTURE, target)


if __name__ == "__main__":
    unittest.main()
