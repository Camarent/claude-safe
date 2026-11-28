"""Return annotations, group 2: every public top-level def of pipeline/plan_check.py and pipeline/plan_check_task.py declares a return type."""

import ast
import os
import unittest

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODULES = [
    os.path.join(TOOLS, "pipeline", "plan_check.py"),
    os.path.join(TOOLS, "pipeline", "plan_check_task.py"),
]


def _public_defs(path):
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), path)
    return [n for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]


class TypingGroup2Test(unittest.TestCase):
    def test_group_covers_32_public_defs(self):
        total = sum(len(_public_defs(path)) for path in MODULES)
        self.assertEqual(total, 32)

    def test_every_public_def_has_return_annotation(self):
        for path in MODULES:
            with self.subTest(module=os.path.basename(path)):
                missing = [n.name for n in _public_defs(path) if n.returns is None]
                self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
