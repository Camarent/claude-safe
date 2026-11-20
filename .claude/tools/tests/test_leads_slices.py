"""Synthetic tests for lead_slices: code lines under cited leads, read fresh, bounded. Temp dirs only."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.briefs import lead_slices
from tests.support.tmpdirs import tracked_tmpdir


class LeadSlices(unittest.TestCase):
    def setUp(self):
        self.tmp = tracked_tmpdir()
        self.d = self.tmp.name
        Path(self.d, "src").mkdir()
        Path(self.d, "src", "a.py").write_text("".join(f"line{i}\n" for i in range(1, 101)), encoding="utf-8")
        Path(self.d, "src", "sub").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_cited_range_is_numbered(self):
        out = lead_slices(self.d, "src/a.py:5-7  reason")
        self.assertIn("src/a.py:5-7", out)
        self.assertIn("5: line5", out)
        self.assertIn("7: line7", out)
        self.assertNotIn("8: line8", out)

    def test_single_line_lead(self):
        out = lead_slices(self.d, "src/a.py:3")
        self.assertIn("3: line3", out)
        self.assertNotIn("4: line4", out)

    def test_missing_file_and_outside_path_skipped(self):
        text = "src/missing.py:1-3\n../outside/x.py:1-3\nsrc/sub:1-2\nsrc/a.py:1-2"
        out = lead_slices(self.d, text)
        self.assertNotIn("missing", out)
        self.assertNotIn("outside", out)
        self.assertNotIn("sub:", out)
        self.assertIn("1: line1", out)

    def test_cap_limits_lines(self):
        out = lead_slices(self.d, "src/a.py:1-100", cap=5)
        self.assertIn("5: line5", out)
        self.assertNotIn("6: line6", out)

    def test_max_slices_respected(self):
        text = "\n".join(f"src/a.py:{n}-{n}" for n in range(1, 8))
        out = lead_slices(self.d, text, max_slices=2)
        self.assertEqual(out.count("src/a.py:"), 2)

    def test_max_chars_respected(self):
        out = lead_slices(self.d, "src/a.py:1-100", cap=100, max_chars=60)
        self.assertLessEqual(len(out), 60)

    def test_unparsable_lines_ignored(self):
        out = lead_slices(self.d, "IDK nothing here\nno colon line\nsrc/a.py:x-y")
        self.assertEqual(out, "")

    def test_empty_input(self):
        self.assertEqual(lead_slices(self.d, ""), "")


if __name__ == "__main__":
    unittest.main()
