"""Verdict parsing: a bare final status word counts, merger BLOCKED and NEED are valid, the retry note names the words, merger.md matches ROLES."""

import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_orch, steps, verdicts
from pipeline.configuration import (
    AGENTS_DIR,
    BLOCKED,
    DEV,
    DONE,
    EV_REPORT,
    EXTRA_ROUND_WHY,
    FAIL,
    MERGER,
    MODEL_SONNET,
    NEED,
    NIT_PASS_WHY,
    PASS,
    REVIEWER,
    ROLES,
)
from pipeline.util import read_jsonl
from test_task_gates import TASK, _SeqBase, dev, done, ev


def rev_dispatch(i):
    return ev("dispatch", id=i, agent="reviewer", role="reviewer", model="haiku", why=None, free=False)


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


NIT_TEXT = "VERDICT: FAIL\n- nit: rename the helper\nwrong 0, risky 0, nit 2\n"
WRONG_TEXT = "VERDICT: FAIL\n- wrong: the guard is off by one\nwrong 1, risky 0, nit 2\n"


class NitOnlyFail(unittest.TestCase):
    def test_counts_line_with_nits_only_is_nit_only(self):
        self.assertTrue(verdicts.is_nit_only_fail(NIT_TEXT))
        self.assertTrue(verdicts.is_nit_only_fail("**wrong** 0, risky 0, nit 1\n"))

    def test_any_wrong_or_risky_is_not_nit_only(self):
        self.assertFalse(verdicts.is_nit_only_fail(WRONG_TEXT))
        self.assertFalse(verdicts.is_nit_only_fail("wrong 0, risky 1, nit 2\n"))
        self.assertFalse(verdicts.is_nit_only_fail("wrong 1 finding at a.py:3\nwrong 0, risky 0, nit 2\n"))

    def test_wrong_bullet_with_zero_counts_is_not_nit_only(self):
        text = "VERDICT: FAIL\n- wrong: the guard is off by one\nwrong 0, risky 0, nit 2\n"
        self.assertFalse(verdicts.is_nit_only_fail(text))

    def test_risky_bullet_with_zero_counts_is_not_nit_only(self):
        text = "VERDICT: FAIL\n- risky: race on the lock file\nwrong 0, risky 0, nit 1\n"
        self.assertFalse(verdicts.is_nit_only_fail(text))

    def test_cited_bullet_with_zero_counts_is_not_nit_only(self):
        text = "VERDICT: FAIL\n- a.py:3 off-by-one in guard\nwrong 0, risky 0, nit 1\n"
        self.assertFalse(verdicts.is_nit_only_fail(text))

    def test_no_nit_count_is_not_nit_only(self):
        self.assertFalse(verdicts.is_nit_only_fail("wrong 0, risky 0, nit 0\n"))
        self.assertFalse(verdicts.is_nit_only_fail("VERDICT: FAIL\nno counts line here\n"))


class NitOnlyAfterFailedRounds(_SeqBase):
    def review_fail(self, i, text):
        """A reviewer FAIL report event whose findings file holds TEXT."""
        f = Path(self.d, f"{i}.md")
        f.write_text(text, encoding="utf-8")
        return ev("report", id=i, role="reviewer", verdict="FAIL", file=str(f))

    def second_fail(self, text):
        return [
            dev("d1"),
            done("d1"),
            rev_dispatch("r1"),
            self.review_fail("r1", "FIRST FINDING text\n"),
            dev("d2", model=MODEL_SONNET),
            done("d2"),
            rev_dispatch("r2"),
            self.review_fail("r2", text),
        ]

    def after(self, mine):
        self.a.orch_events = mine
        s = steps._TaskState(self.a, TASK, mine)
        return steps._after_reviewer(self.a, TASK, mine, "a9", s, mine[-1])

    def test_nit_only_second_fail_passes_with_notes_and_logs_the_nits_file(self):
        with mock.patch.object(steps, "append_event") as log:
            r = self.after(self.second_fail(NIT_TEXT))
        self.assertIsNone(r)  # the task passes: no extra round, no lead
        log.assert_called_once()
        e = log.call_args.args[1]
        self.assertEqual((e["ev"], e["role"], e["verdict"], e["task"]), (EV_REPORT, "reviewer", PASS, TASK["id"]))
        self.assertEqual(e["reason"], NIT_PASS_WHY)
        self.assertEqual(e["file"], str(Path(self.d, "r2.md")))

    def test_wrong_fail_still_gets_the_extra_round(self):
        with mock.patch.object(steps, "append_event") as log:
            r = self.after(self.second_fail(WRONG_TEXT))
        log.assert_not_called()
        self.assertEqual((r["action"], r["role"]), ("dispatch", "dev"))
        self.assertIn(EXTRA_ROUND_WHY, r["why"])

    def test_mutation_flipping_the_counts_line_flips_the_outcome(self):
        """Same findings, only the counts line changed: nit-only passes, a wrong finding takes the extra round."""
        with mock.patch.object(steps, "append_event"):
            self.assertIsNone(self.after(self.second_fail(NIT_TEXT)))
        mutated = NIT_TEXT.replace("wrong 0, risky 0, nit 2", "wrong 1, risky 0, nit 2")
        r = self.after(self.second_fail(mutated))
        self.assertEqual(r["action"], "dispatch")
        self.assertIn(EXTRA_ROUND_WHY, r["why"])


if __name__ == "__main__":
    unittest.main()
