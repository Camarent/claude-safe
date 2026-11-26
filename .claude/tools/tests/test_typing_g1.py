"""Return annotations on the public defs of worktree, worktree_base and worktree_merge (group 1 of the typing split): every module-level or method def not starting with `_` names its return type."""

import ast
import os
import unittest

PIPELINE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline")
MODULES = ("worktree.py", "worktree_base.py", "worktree_merge.py")


def unannotated_public(source):
    """-> names of the module-level and method defs in SOURCE, not starting with `_`, that have no return annotation."""
    tree = ast.parse(source)
    found = []
    for parent in ast.walk(tree):
        if isinstance(parent, (ast.Module, ast.ClassDef)):
            for node in parent.body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_") and node.returns is None:
                    found.append(node.name)
    return found


class ReturnAnnotations(unittest.TestCase):
    def test_public_defs_have_return_annotation(self):
        for name in MODULES:
            with self.subTest(module=name), open(os.path.join(PIPELINE, name), encoding="utf-8") as fh:
                self.assertEqual(unannotated_public(fh.read()), [], f"{name}: public def without a return annotation")

    def test_checker_flags_missing_annotation(self):
        src = "def f(x) -> int:\n    return x\n\ndef g(x):\n    return x\n\ndef _h(x):\n    return x\n\nclass C:\n    def m(self):\n        return 1\n"
        self.assertEqual(unannotated_public(src), ["g", "m"])


if __name__ == "__main__":
    unittest.main()
