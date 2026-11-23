"""Retry notes must name the file, the line and the replacement text: the runbook and the orch docstring say so."""

import os
import pathlib
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_orch

PHRASE = "replacement text"
REPO = pathlib.Path(__file__).resolve().parents[3]


class RetryNoteHelpTest(unittest.TestCase):
    def test_docstring_names_replacement_text(self):
        self.assertIn(PHRASE, cli_orch.__doc__ or "")

    def test_runbook_names_replacement_text(self):
        text = (REPO / ".claude" / "ORCHESTRATOR.md").read_text(encoding="utf-8")
        self.assertIn(PHRASE, text)

    def test_note_help_names_replacement_text(self):
        source = pathlib.Path(cli_orch.__file__).read_text(encoding="utf-8")
        self.assertIn(PHRASE, source)


if __name__ == "__main__":
    unittest.main()
