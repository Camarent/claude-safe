"""A reviewer ESCALATE with zero counts is a PASS only without an `ESCALATE:` line and with the counts on their own line.

A reviewer ESCALATE that names no defect at file:line is FAIL with the stub reason, so the auto retry runs the reviewer once more.
"""

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
        self.assertEqual(self.v("the rubric says `wrong 0, risky 0, nit 0` when clean\n- tools/x.py:3 unsure\nVERDICT: ESCALATE"), "ESCALATE")

    def test_counts_line_alone_still_passes(self):
        self.assertEqual(self.v("wrong 0, risky 0, nit 0\nVERDICT: ESCALATE"), "PASS")

    def test_zero_counts_pass_with_notes_name_report_id(self):
        text = "wrong 0, risky 0, nit 0\nnotes: style only\nVERDICT: ESCALATE"
        self.assertEqual(
            verdicts.verdict("reviewer", text, "a2"),
            ("PASS", "escalate_downgraded: ESCALATE with wrong 0, risky 0, nit 0 and no security line: PASS with notes, report a2"),
        )

    def test_risky_nonzero_stays_escalate(self):
        self.assertEqual(self.v("wrong 0, risky 1, nit 0\n- tools/x.py:3 race on close\nVERDICT: ESCALATE"), "ESCALATE")

    def test_nit_nonzero_stays_escalate(self):
        self.assertEqual(self.v("wrong 0, risky 0, nit 1\n- tools/x.py:3 rename foo\nVERDICT: ESCALATE"), "ESCALATE")

    def test_wrong_nonzero_stays_escalate(self):
        self.assertEqual(self.v("wrong 1, risky 0, nit 0\n- tools/x.py:3 bug\nVERDICT: ESCALATE"), "ESCALATE")

    def test_wrong_zero_with_wrong_nonzero_elsewhere_stays_escalate(self):
        self.assertEqual(self.v("wrong 0, risky 0, nit 0\nwrong 2: a real bug in x.py:3\nVERDICT: ESCALATE"), "ESCALATE")

    def test_wrong_zero_with_security_line_stays_escalate(self):
        self.assertEqual(self.v("ESCALATE: tools/x.py:12 shell=True with input\nwrong 0, risky 1, nit 0\nVERDICT: ESCALATE"), "ESCALATE")

    def test_escalate_with_defect_at_file_line_stays_escalate(self):
        self.assertEqual(self.v("- tools/x.py:3 the loop leaks the handle\nVERDICT: ESCALATE"), "ESCALATE")

    def test_escalate_without_defect_line_is_fail_stub(self):
        self.assertEqual(verdicts.verdict("reviewer", "- the loop may leak\nVERDICT: ESCALATE"), ("FAIL", verdicts.STUB_REASON))

    def test_dev_escalate_with_wrong_zero_untouched(self):
        self.assertEqual(verdicts.verdict("dev", "wrong 0, risky 0, nit 0\nVERDICT: ESCALATE")[0], "ESCALATE")


if __name__ == "__main__":
    unittest.main()
