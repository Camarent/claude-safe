"""A reviewer ESCALATE with zero counts is a PASS only without an `ESCALATE:` line and with the counts on their own line."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import verdicts


class EscalateZero(unittest.TestCase):
    def v(self, text):
        return verdicts.verdict("reviewer", text)[0]

    def test_security_escalate_line_stays_escalate(self):
        self.assertEqual(self.v("ESCALATE: tools/x.py:12 subprocess with variable\nwrong 0, risky 0, nit 0\nVERDICT: ESCALATE"), "ESCALATE")

    def test_quoted_counts_inside_text_do_not_count(self):
        self.assertEqual(self.v("the rubric says `wrong 0, risky 0, nit 0` when clean, but I am unsure\nVERDICT: ESCALATE"), "ESCALATE")

    def test_counts_line_alone_still_passes(self):
        self.assertEqual(self.v("wrong 0, risky 0, nit 0\nVERDICT: ESCALATE"), "PASS")


if __name__ == "__main__":
    unittest.main()
