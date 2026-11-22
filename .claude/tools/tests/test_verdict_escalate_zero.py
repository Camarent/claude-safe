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

    def test_wrong_zero_among_last_lines_passes_with_notes(self):
        text = "- nit: rename foo to bar\nwrong 0, risky 0, nit 1\nnotes: style only\nVERDICT: ESCALATE"
        self.assertEqual(verdicts.verdict("reviewer", text), ("PASS", "escalate_downgraded: ESCALATE with wrong 0 and no security line: PASS with notes"))

    def test_risky_nonzero_stays_escalate(self):
        self.assertEqual(self.v("wrong 0, risky 1, nit 0\nVERDICT: ESCALATE"), "ESCALATE")

    def test_wrong_nonzero_stays_escalate(self):
        self.assertEqual(self.v("wrong 1, risky 0, nit 0\nVERDICT: ESCALATE"), "ESCALATE")

    def test_wrong_zero_with_wrong_nonzero_elsewhere_stays_escalate(self):
        self.assertEqual(self.v("wrong 0, risky 0, nit 0\nwrong 2: a real bug in x.py:3\nVERDICT: ESCALATE"), "ESCALATE")

    def test_wrong_zero_with_security_line_stays_escalate(self):
        self.assertEqual(self.v("ESCALATE: tools/x.py:12 shell=True with input\nwrong 0, risky 1, nit 0\nVERDICT: ESCALATE"), "ESCALATE")

    def test_dev_escalate_with_wrong_zero_untouched(self):
        self.assertEqual(verdicts.verdict("dev", "wrong 0, risky 0, nit 0\nVERDICT: ESCALATE")[0], "ESCALATE")


if __name__ == "__main__":
    unittest.main()
