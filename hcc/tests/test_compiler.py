from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from hcc import compile_path, dump_program


class HccCompilerTests(unittest.TestCase):
    def test_compiles_function_call_into_readable_helix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "smoke.c"
            source_path.write_text(
                textwrap.dedent(
                    """\
                    int add(int a, int b) { return a + b; }

                    int main(void) {
                        int x = 40;
                        int y = add(x, 2);
                        return y;
                    }
                    """,
                ),
                encoding="utf-8",
            )

            helix = dump_program(compile_path(source_path, use_cpp=False))

        self.assertIn("c:", helix)
        self.assertIn("c_add:", helix)
        self.assertIn("main: [call, c.main, []]", helix)
        self.assertIn("- [set, y, [call, c_add, [x, 2]]]", helix)
        self.assertIn("- [return, [add, a, b]]", helix)


if __name__ == "__main__":
    unittest.main()
