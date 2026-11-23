"""The no-skip rule for opt-in tests is written into the python guideline and the dev/architect briefs."""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

RULE_FILES = [
    ".claude/practices/guidelines/python.md",
    ".claude/agents/dev.md",
    ".claude/agents/architect.md",
]


class SkipRuleDocsTest(unittest.TestCase):
    def test_rule_file_states_deselect_not_skipif(self):
        for rel in RULE_FILES:
            with self.subTest(file=rel):
                text = (ROOT / rel).read_text(encoding="utf-8")
                self.assertIn("deselect", text)
                self.assertIn("skipif", text)


if __name__ == "__main__":
    unittest.main()
