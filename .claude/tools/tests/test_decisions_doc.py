import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO = Path(__file__).resolve().parents[3]
DECISIONS = REPO / "docs" / "decisions.md"
README = REPO / "README.md"
PROBLEMS = range(1, 14)


def reasons(text):
    """Map problem number to the body under its '## Problem N' heading."""
    parts = re.split(r"^## Problem (\d+)\s*$", text, flags=re.M)
    return {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts), 2)}


class DecisionsDocTest(unittest.TestCase):
    def test_every_problem_has_reason(self):
        found = reasons(DECISIONS.read_text())
        for n in PROBLEMS:
            self.assertTrue(found.get(n), f"Problem {n} missing or empty")

    def test_missing_heading_detected(self):
        text = DECISIONS.read_text().replace("## Problem 7\n", "")
        self.assertNotIn(7, reasons(text))

    def test_readme_mentions_notes(self):
        text = README.read_text()
        for needle in ("sync-claude-config.sh", "retry --verify", "leanup"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main()
