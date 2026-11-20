"""audit_context._sym_line: the same refs in any input order give the same line (synthetic, lp patched)."""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from audit import audit_context as C
from search import lsp

REFS_A = ["z/last.py:4  call", "m/mid.py:9  call", "a/first.py:2  call"]
REFS_B = ["a/first.py:2  call", "z/last.py:4  call", "m/mid.py:9  call"]


def _line_for(refs):
    with mock.patch("search.leadparts.lp", return_value="\n".join(refs) + "\n"):
        return C._sym_line("/repo", "src/own.py", "foo")


class TestSymLineOrder(unittest.TestCase):
    def test_same_refs_any_order_same_line(self):
        self.assertEqual(_line_for(REFS_A), _line_for(REFS_B))

    def test_files_listed_sorted(self):
        self.assertEqual(_line_for(REFS_A), "  foo: 3 files: a/first.py, m/mid.py, z/last.py")


class TestSymLineRealRefsFormat(unittest.TestCase):
    """The refs text is produced by the real `lsp.show` (the printer of `lp nav refs`), so a format change there breaks this parse test."""

    def test_parses_what_lsp_show_prints(self):
        with tempfile.TemporaryDirectory() as root:
            items = []
            for name, text in (("own.py", "def foo():\n"), ("b/use.py", "x = foo()\n"), ("a/use2.py", "foo()\n"), ("a/use2.py", "y = foo()\n")):
                path = os.path.join(root, name)
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(text)
                items.append((path, 1 if name != "a/use2.py" else 2, 1))
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                lsp.show(items, root)
            with mock.patch("search.leadparts.lp", return_value=buf.getvalue()):
                self.assertEqual(C._sym_line(root, "own.py", "foo"), "  foo: 2 files: a/use2.py, b/use.py")


if __name__ == "__main__":
    unittest.main()
