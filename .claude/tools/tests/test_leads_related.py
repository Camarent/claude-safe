"""Synthetic tests for related_files: docs and tests of the led files, names only. Temp dirs only."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.briefs import related_files
from tests.support.tmpdirs import tracked_tmpdir


class RelatedFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tracked_tmpdir()
        self.d = self.tmp.name
        Path(self.d, "src").mkdir()
        Path(self.d, "src", "a.py").write_text("def f():\n    return 1\n", encoding="utf-8")
        Path(self.d, "tests").mkdir()
        Path(self.d, "tests", "test_a.py").write_text("import a\n", encoding="utf-8")
        Path(self.d, "docs").mkdir()
        Path(self.d, "docs", "a.md").write_text("See `a.py` for the function.\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_names_related_docs_and_tests(self):
        out = related_files(self.d, "src/a.py:1-2  reason", "task text")
        self.assertIn("tests/test_a.py", out)
        self.assertIn("docs/a.md", out)
        self.assertTrue(out.startswith(" Related docs and tests of these files"))
        self.assertTrue(out.endswith(".\n"))

    def test_no_related_files_gives_empty(self):
        Path(self.d, "tests", "test_a.py").unlink()
        Path(self.d, "docs", "a.md").unlink()
        self.assertEqual(related_files(self.d, "src/a.py:1-2  reason", ""), "")

    def test_missing_lead_file_is_ignored(self):
        self.assertEqual(related_files(self.d, "src/missing.py:1-2  reason", ""), "")
        out = related_files(self.d, "src/missing.py:1-2  reason\nsrc/a.py:1  reason", "")
        self.assertIn("tests/test_a.py", out)
        self.assertNotIn("missing", out)

    def test_exception_gives_empty(self):
        self.assertEqual(related_files(os.path.join(self.d, "no", "such", "dir"), None, None), "")


if __name__ == "__main__":
    unittest.main()
