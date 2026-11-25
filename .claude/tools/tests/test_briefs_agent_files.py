"""The four agent files (architect, dev, reviewer, scout) each carry the Bash rule line; dev.md also carries the tooling-BLOCKED and CCN rules."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

AGENTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agents"))
AGENT_FILES = ["architect.md", "dev.md", "reviewer.md", "scout.md"]
BASH_PHRASES = [
    "One Bash command per call",
    "`git -C <dir>` never `cd <dir>`",
    "No `;`, `&&`, `|` chains outside the qrun form",
    "Model set on every Agent call",
]
DEV_PHRASES = [
    "A BLOCKED on a tooling failure names the cause",
    "A fix task with a CCN goal never runs cs-audit",
]


def missing_phrases(text, phrases):
    """The PHRASES that TEXT lacks; empty when the text carries all of them."""
    return [p for p in phrases if p not in text]


def _read(name):
    with open(os.path.join(AGENTS_DIR, name), encoding="utf-8") as f:
        return f.read()


class AgentFilesTest(unittest.TestCase):
    def test_every_agent_file_states_every_bash_rule(self):
        for name in AGENT_FILES:
            with self.subTest(agent=name):
                self.assertEqual(missing_phrases(_read(name), BASH_PHRASES), [])

    def test_dev_file_states_tooling_block_and_ccn_rules(self):
        self.assertEqual(missing_phrases(_read("dev.md"), DEV_PHRASES), [])

    def test_checker_reports_file_without_git_c_rule(self):
        text = _read("scout.md")
        broken = text.replace("`git -C <dir>` never `cd <dir>`", "")
        self.assertEqual(missing_phrases(broken, BASH_PHRASES), ["`git -C <dir>` never `cd <dir>`"])


if __name__ == "__main__":
    unittest.main()
