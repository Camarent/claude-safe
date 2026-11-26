"""plan-reviewer.md rules: GAP for uncited symbols and for external-signal Done lines; front matter kept."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

AGENT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "agents", "plan-reviewer.md")


class PlanReviewerRules(unittest.TestCase):
    def setUp(self):
        with open(AGENT, encoding="utf-8") as f:
            self.text = f.read()

    def test_uncited_symbol_gap_phrase(self):
        self.assertIn("uncited symbol", self.text)

    def test_external_signal_done_gap_phrase(self):
        self.assertIn("what code must change", self.text)

    def test_front_matter_unchanged(self):
        front = self.text.split("\n---\n", 1)[0]
        self.assertTrue(front.startswith("---\nname: plan-reviewer\n"), front)
        self.assertIn("\nmodel: haiku", front)

    def test_report_format_lines_unchanged(self):
        self.assertIn("- one line per gap: `GAP: <text>`", self.text)
        self.assertIn("`VERDICT: DONE` (no gaps), `VERDICT: FAIL` (one or more gaps)", self.text)


if __name__ == "__main__":
    unittest.main()
