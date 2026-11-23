"""A reviewer FAIL with no finding line is unclear: the auto retry runs the reviewer once more before any rework."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_orch, verdicts
from pipeline.configuration.verdicts import STUB_REASON

STUB = "VERDICT: FAIL\nwrong 2, risky 0, nit 0\nREPORT a11"
WITH_FINDING = "- wrong: pipeline/x.py:12 off-by-one in the loop\n" + STUB


class VerdictStub(unittest.TestCase):
    def test_stub_fail_is_unclear(self):
        v, reason = verdicts.verdict("reviewer", STUB)
        self.assertEqual((v, reason), ("FAIL", STUB_REASON))
        self.assertTrue(verdicts.is_unclear(reason))

    def test_fail_with_finding_line_is_not_unclear(self):
        v, reason = verdicts.verdict("reviewer", WITH_FINDING)
        self.assertEqual((v, reason), ("FAIL", "report line"))
        self.assertFalse(verdicts.is_unclear(reason))

    def test_pass_with_counts_only_is_not_unclear(self):
        v, reason = verdicts.verdict("reviewer", "VERDICT: PASS\nwrong 0, risky 0, nit 0\nREPORT a11")
        self.assertEqual((v, reason), ("PASS", "report line"))
        self.assertFalse(verdicts.is_unclear(reason))

    def test_stub_rule_is_reviewer_only(self):
        v, reason = verdicts.verdict("tester", "VERDICT: FAIL\nREPORT a11")
        self.assertEqual((v, reason), ("FAIL", "report line"))

    def test_missing_note_names_the_finding_list(self):
        note = verdicts.missing_note("reviewer", STUB_REASON)
        self.assertEqual(note, "the previous FAIL report had no finding: list each finding as file:line, scenario, severity")

    def test_auto_retry_logged_once(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        log = os.path.join(tmp.name, "events.jsonl")
        d = {"role": "reviewer", "task": "task-6", "id": "a11", "agent": "reviewer"}
        self.assertTrue(cli_orch._auto_retry(log, [], d, STUB_REASON))
        evs = [{"ev": "retry", "auto": True, "task": "task-6", "role": "reviewer"}]
        self.assertFalse(cli_orch._auto_retry(log, evs, d, STUB_REASON))


if __name__ == "__main__":
    unittest.main()
