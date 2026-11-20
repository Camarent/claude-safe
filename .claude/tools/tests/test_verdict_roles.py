"""Verdict parsing: a bare final status word counts, merger BLOCKED and NEED are valid, the retry note names the words, merger.md matches ROLES."""

import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_orch, verdicts
from pipeline.configuration import AGENTS_DIR, BLOCKED, DEV, DONE, FAIL, MERGER, NEED, REVIEWER, ROLES
from pipeline.util import read_jsonl


class FinalStatusWord(unittest.TestCase):
    def test_bare_final_word_accepted(self):
        self.assertEqual(verdicts.verdict(DEV, "Changed the answer block.\nDONE"), (DONE, "final status line"))

    def test_bold_final_word_accepted(self):
        self.assertEqual(verdicts.verdict(REVIEWER, "one finding in x.py:3\n**FAIL**"), (FAIL, "final status line"))

    def test_final_word_not_allowed_for_role_is_unclear(self):
        self.assertEqual(verdicts.verdict(REVIEWER, "looks fine\nDONE")[0], FAIL)

    def test_word_in_middle_is_not_a_final_word(self):
        self.assertEqual(verdicts.verdict(REVIEWER, "FAIL is not the end\nmore text here")[0], FAIL)
        self.assertEqual(verdicts.verdict(DEV, "DONE\nmore text here")[1], "no clear verdict")


class MergerVerdicts(unittest.TestCase):
    def test_blocked_is_valid_for_merger(self):
        self.assertEqual(verdicts.verdict(MERGER, "conflict-resolve cannot resolve x.py\nVERDICT: BLOCKED"), (BLOCKED, "report line"))

    def test_need_line_flags_blocked_even_with_fail_line(self):
        v, reason = verdicts.verdict(MERGER, "NEED: commit.py denied: permission\nVERDICT: FAIL")
        self.assertEqual(v, BLOCKED)
        self.assertIn("denied", reason)

    def test_need_line_without_verdict_line_is_blocked(self):
        self.assertEqual(verdicts.verdict(MERGER, "**NEED:** brief has no commit trailer")[0], BLOCKED)

    def test_merger_agent_words_match_driver(self):
        with open(os.path.join(AGENTS_DIR, "merger.md"), encoding="utf-8") as f:
            words = set(re.findall(r"VERDICT: (DONE\|FAIL\|BLOCKED)", f.read())[0].split("|"))
        self.assertEqual(words, set(ROLES[MERGER][0]))
        self.assertEqual(NEED, "NEED")


class RetryNote(unittest.TestCase):
    def test_missing_note_names_the_line_and_the_words(self):
        note = verdicts.missing_note(DEV, "no clear verdict")
        self.assertIn("`VERDICT:`", note)
        self.assertIn("DONE|ESCALATE|BLOCKED", note)

    def test_auto_retry_logs_the_missing_words(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        log = os.path.join(tmp, "orch.jsonl")
        d = {"id": "a2", "task": "task-1", "role": DEV, "agent": "dev"}
        self.assertTrue(cli_orch._auto_retry(log, [], d, "no clear verdict"))
        retry = [e for e in read_jsonl(log) if e["ev"] == "retry"][0]
        self.assertIn("DONE|ESCALATE|BLOCKED", retry["note"])


class WebTesterUnverifiedTest(unittest.TestCase):
    def test_partial_pass_is_blocked(self):
        for text in ("VERDICT: PASS (partial)\nitems 3-4 not run", "VERDICT: PASS\nUNVERIFIED: items 3-4 (serve denied)"):
            self.assertEqual(verdicts.verdict("web-tester", text)[0], BLOCKED, text)

    def test_full_pass_and_fail_unchanged(self):
        self.assertEqual(verdicts.verdict("web-tester", "VERDICT: PASS\nall 4 items ok")[0], "PASS")
        self.assertEqual(verdicts.verdict("web-tester", "VERDICT: FAIL\nUNVERIFIED: x")[0], "FAIL")
        self.assertEqual(verdicts.verdict("tester", "VERDICT: PASS\nUNVERIFIED: x")[0], "PASS")


if __name__ == "__main__":
    unittest.main()
