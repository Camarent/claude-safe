"""plan_rules.check_negative_tag: a negative grep on a bare markup tag is a `neg:` warning, never an error (synthetic task files)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_rules


class NegTag(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.d = td.name

    def lint(self, line):
        with open(os.path.join(self.d, "task-1.md"), "w", encoding="utf-8") as f:
            f.write(f"Predicted paths: a.html\nDone:\nC1 {line}\n")
        return plan_rules.check_negative_tag(self.d, "task-1")

    def test_bare_tag_hits(self):
        for line in ('! grep -q "<script" a.html', "grep -L '<style' a.html", 'grep -vq "<div" a.html', 'test ! -e x || ! grep "<div"" a'):
            got = self.lint(line)
            self.assertEqual(len(got), 1, line)
            self.assertTrue(got[0].startswith("neg: task-1"))
            self.assertEqual(plan_check.errors_of(got), [])

    def test_escaped_quote_form_hits_and_ordinary_code_fragments_do_not(self):
        self.assertEqual(len(self.lint('sh -c "! grep -q \\"<script\\" a.html"')), 1)
        for line in ('! grep -q "if (a <b" x.js', '! grep -E "Vec<String" x.rs', "! grep -rn 'a<b' src", '! grep -c "x <y" f'):
            self.assertEqual(self.lint(line), [], line)

    def test_clean_lines(self):
        for line in (
            '! grep -q "<script src=" a.html',
            '! grep -q "<script>" a.html',
            'grep -q "<script" a.html',
            '! grep -q "todo" a.html',
            "! grep -q 'a < b' a.html",
        ):
            self.assertEqual(self.lint(line), [], line)


if __name__ == "__main__":
    unittest.main()
