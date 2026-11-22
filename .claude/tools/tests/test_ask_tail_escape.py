"""cli_wave._ask_tail shows non-printable characters of the report tail as \\uXXXX escapes."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_wave

ZWSP = "\N{ZERO WIDTH SPACE}"
RLO = "\N{RIGHT-TO-LEFT OVERRIDE}"
BEL = "\x07"


class AskTailEscape(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = t.name

    def report(self, text):
        path = os.path.join(self.d, "report.md")
        Path(path).write_text(text, encoding="utf-8")
        return path

    def test_zero_width_and_bidi_are_escaped(self):
        path = self.report(f"ok line\nbad {ZWSP}zero{RLO}bidi\tkept\n")
        tail = cli_wave._ask_tail(f"step: review failed in {path}")
        self.assertIn("\\u200B", tail)
        self.assertIn("\\u202E", tail)
        self.assertNotIn(ZWSP, tail)
        self.assertNotIn(RLO, tail)
        self.assertIn("bad ", tail)
        self.assertIn("\tkept", tail)

    def test_control_char_is_escaped(self):
        path = self.report(f"first{BEL}bell\n")
        tail = cli_wave._ask_tail(f"step: cause in {path}")
        self.assertIn("\\u0007", tail)
        self.assertNotIn(BEL, tail)

    def test_plain_text_tail_unchanged(self):
        path = self.report("alpha\nbeta gamma\n")
        tail = cli_wave._ask_tail(f"step: cause in {path}")
        self.assertEqual(tail, f"error tail, {path}:\n    alpha\n    beta gamma")

    def test_no_report_named(self):
        self.assertEqual(cli_wave._ask_tail("step: cause"), "error tail: none, the ask names no report file")
