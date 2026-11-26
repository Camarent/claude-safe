"""guidelines.digest: the guideline sections for the changed file types, from a fixture guidelines folder."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.guidelines import digest


class DigestTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.gdir = t.name
        self.write("base.md", ["BASE LINE"])
        self.write("python.md", ["PY LINE"])
        self.write("js.md", ["JS LINE"])

    def write(self, name, lines):
        with open(os.path.join(self.gdir, name), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")

    def digest(self, *paths):
        return digest(self.gdir, list(paths), self.gdir)

    def test_python_change_omits_js_section(self):
        out = self.digest("pipeline/a.py")
        self.assertIn("BASE LINE", out)
        self.assertIn("PY LINE", out)
        self.assertNotIn("JS LINE", out)
        self.assertIn(f"full guideline: {os.path.join(self.gdir, 'python.md')}", out)
        self.assertIn(f"full guideline: {os.path.join(self.gdir, 'base.md')}", out)

    def test_js_change_includes_js_section(self):
        out = self.digest("ui/app.mjs")
        self.assertIn("JS LINE", out)
        self.assertNotIn("PY LINE", out)

    def test_mixed_change_includes_both_sections(self):
        out = self.digest("a.py", "b.js")
        self.assertIn("PY LINE", out)
        self.assertIn("JS LINE", out)

    def test_no_match_prints_full_path_list_only(self):
        out = self.digest("notes.txt", "Makefile")
        self.assertEqual(out, [os.path.join(self.gdir, f) for f in ("base.md", "js.md", "python.md")])

    def test_digest_capped_at_sixty_lines_and_keeps_pointers(self):
        self.write("base.md", [f"base {i}" for i in range(200)])
        out = self.digest("a.py")
        self.assertLessEqual(len(out), 60)
        self.assertIn(f"full guideline: {os.path.join(self.gdir, 'base.md')}", out)
        self.assertIn(f"full guideline: {os.path.join(self.gdir, 'python.md')}", out)
        self.assertIn("... cut: read the full guideline files above", out)

    def test_short_digest_has_no_cut_marker(self):
        out = self.digest("a.py")
        self.assertNotIn("... cut: read the full guideline files above", out)
