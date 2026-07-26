import re
import unittest
from pathlib import Path

from ruamel.yaml import YAML

from tui import hui_demo


FIXTURE = Path(__file__).parents[1] / "hui_demo.yaml"
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

    def test_viewport_clips_to_fixed_dimensions(self):
        doc = document()
        state = hui_demo.DemoState(selection=("state", "frames", 0, 0))
        rendered = hui_demo.render(doc, state, 7, 31)
        visible = ANSI.sub("", rendered).splitlines()
        self.assertEqual(len(visible), 7)
        self.assertTrue(all(len(line) == 31 for line in visible))


class ReducerTests(unittest.TestCase):
    def test_semantic_traversal_parent_child_and_viewport(self):
        doc = document()
        state = hui_demo.DemoState(selection=())
        state, _ = hui_demo.reduce_state(doc, state, hui_demo.RIGHT, 5)
        self.assertEqual(state.selection, ("keybinds",))
        state, _ = hui_demo.reduce_state(doc, state, hui_demo.DOWN, 5)
        self.assertEqual(state.selection, ("keybinds", 0))
        state, _ = hui_demo.reduce_state(doc, state, hui_demo.LEFT, 5)
        self.assertEqual(state.selection, ("keybinds",))
        state, _ = hui_demo.reduce_state(doc, state, hui_demo.PAGE_DOWN, 5)
        self.assertEqual(state.viewport, 5)

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
        self.assertEqual(state.selection, ("workspace", "main"))
        state, _ = hui_demo.reduce_state(doc, state, "R", 20)
        self.assertEqual(state.context_vm, ())
        self.assertEqual(state.selection, ("main",))


class RuntimeAndTerminalTests(unittest.TestCase):
    def test_f10_and_f5_delegate_and_reload_yaml(self):
        calls = []
        yaml = YAML(typ="safe")
        initial = yaml.load(FIXTURE.read_text(encoding="utf-8"))

        def runtime(operation, binary_path, target_path):
            calls.append((operation, binary_path, target_path))
            changed = dict(initial)
            changed["global-value"] = len(calls)
            return changed

        terminal = MemoryTerminal([hui_demo.F10, hui_demo.F5, hui_demo.CTRL_Q])
        binary = Path("build/helix")
        hui_demo.run_demo(FIXTURE, binary, terminal, runtime)
        self.assertEqual([call[0] for call in calls], ["step", "start"])
        self.assertTrue(terminal.restored)
        self.assertGreaterEqual(len(terminal.output), 3)

    def test_terminal_restores_after_input_error(self):
        terminal = MemoryTerminal([], raise_on_read=True)
        with self.assertRaisesRegex(RuntimeError, "input failed"):
            hui_demo.run_demo(FIXTURE, Path("build/helix"), terminal)
        self.assertTrue(terminal.entered)
        self.assertTrue(terminal.restored)


if __name__ == "__main__":
    unittest.main()
