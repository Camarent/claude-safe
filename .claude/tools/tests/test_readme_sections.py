"""README.md: the section headings readiness.md asks for, and one plain line per invented name."""

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
README = REPO / "README.md"
# Heading prefixes the public README must carry (readiness.md lines 28-37).
REQUIRED_HEADINGS = ("What", "Requirements", "Quick start", "Pipeline", "License")
# Invented names a newcomer meets; each needs its own line that says what it is.
INVENTED_NAMES = ("codenames.py", "retro", "strata")


def headings(text):
    """Return the text of every level-2 heading."""
    return re.findall(r"^## (.+?)\s*$", text, flags=re.M)


def missing_headings(text):
    """Return the required heading prefixes that no level-2 heading starts with."""
    found = headings(text)
    return [want for want in REQUIRED_HEADINGS if not any(h.startswith(want) for h in found)]


def name_lines(text, name):
    """Return the lines that mention an invented name."""
    return [line for line in text.splitlines() if name in line]


class ReadmeSectionsTest(unittest.TestCase):
    def setUp(self):
        self.text = README.read_text()

    def test_required_headings_present(self):
        self.assertEqual(missing_headings(self.text), [], "README.md lacks required '## ' headings")

    def test_missing_quick_start_detected(self):
        # Mutation: without the Quick start heading the same check must report it.
        mutated = re.sub(r"^## Quick start\s*$", "## Start", self.text, flags=re.M)
        self.assertIn("Quick start", missing_headings(mutated))

    def test_each_invented_name_has_a_line(self):
        for name in INVENTED_NAMES:
            lines = name_lines(self.text, name)
            self.assertTrue(lines, f"README.md does not name {name}")
            self.assertTrue(any(len(line) > len(name) + 10 for line in lines), f"{name} has no explanation")

    def test_install_names_wrapper_dir(self):
        self.assertIn(".claude/bin", self.text)
        self.assertIn("cs-", self.text)


if __name__ == "__main__":
    unittest.main()
