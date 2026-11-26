"""README.md Safety section and the docs/decisions.md entry that records the safety choices."""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
README = REPO / "README.md"
DECISIONS = REPO / "docs" / "decisions.md"
SAFETY_HEADING = "## Safety"
# Words and flag names the Safety section must carry (task-9 C1).
SAFETY_NEEDLES = (
    "auto mode",
    "starts as root",
    "network",
    "read-write",
    "--with-gh-token",
    "--with-cloudflare-token",
    "scripts/pipeline_auto.py",
)
# Words the decisions entry must carry (task-9 C2).
DECISION_NEEDLES = ("settings.json", "allows", "git commit")


def safety_body(text):
    """Return the text under the level-2 Safety heading, up to the next level-2 heading, or None."""
    match = re.search(r"^## Safety\s*$\n(.*?)(?=^## |\Z)", text, flags=re.M | re.S)
    return match.group(1) if match else None


class ReadmeSafetyTest(unittest.TestCase):
    def setUp(self):
        self.text = README.read_text()

    def test_safety_heading_present(self):
        self.assertIn(SAFETY_HEADING, self.text.splitlines())

    def test_safety_section_names_each_point(self):
        body = safety_body(self.text)
        if body is None:
            self.fail("README.md has no '## Safety' section")
        for needle in SAFETY_NEEDLES:
            self.assertIn(needle, body, f"Safety section does not name {needle}")


class DecisionsSafetyTest(unittest.TestCase):
    def test_settings_decision_recorded(self):
        text = DECISIONS.read_text()
        self.assertIn("settings.json allows", text)
        for needle in DECISION_NEEDLES:
            self.assertIn(needle, text, f"docs/decisions.md does not name {needle}")


if __name__ == "__main__":
    unittest.main()
