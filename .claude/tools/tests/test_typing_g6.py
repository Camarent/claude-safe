"""Return annotations on the public functions of briefs, pr_ops and pr_release (group 6 of the typing split): every top-level def not starting with `_` names its return type."""

import ast
import os
import unittest

PIPELINE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "pipeline")
MODULES = ("briefs.py", "pr_ops.py", "pr_release.py")


def unannotated_public(source):
    """-> names of the top-level public defs in SOURCE that have no return annotation."""
    tree = ast.parse(source)
    return [
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_") and node.returns is None
    ]


class ReturnAnnotations(unittest.TestCase):
    def test_public_defs_have_return_annotation(self):
        for name in MODULES:
            with self.subTest(module=name), open(os.path.join(PIPELINE, name), encoding="utf-8") as fh:
                self.assertEqual(unannotated_public(fh.read()), [], f"{name}: public def without a return annotation")

    def test_checker_flags_missing_annotation(self):
        src = "def f(x) -> int:\n    return x\n\ndef g(x):\n    return x\n\ndef _h(x):\n    return x\n"
        self.assertEqual(unannotated_public(src), ["g"])


if __name__ == "__main__":
    unittest.main()
