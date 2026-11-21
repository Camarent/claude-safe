"""A reviewer finding `PLAN-ERROR: <fix>` goes to the architect (one plan fix per task), then the reviewer runs again; neither counts against the retry budget."""

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import steps
from test_task_gates import _SeqBase, dev, done, ev


def reviewer(i, model="haiku"):
    return ev("dispatch", id=i, agent="reviewer", role="reviewer", model=model, why=None, free=False)


class PlanErrorTest(_SeqBase):
    def report(self, name, text):
        p = Path(self.d, name)
        p.write_text(text, encoding="utf-8")
        return str(p)

    def fail(self, i, text="VERDICT: FAIL\nwrong 1\n"):
        return ev("report", id=i, role="reviewer", verdict="FAIL", file=self.report(f"r-{i}.md", text))

    def architect(self, i):
        return ev("dispatch", id=i, agent="architect", role="architect", model=None, why=None, free=True)

    def test_regex_needs_own_line(self):
        self.assertTrue(steps.PLAN_ERROR_RE.search("VERDICT: FAIL\nPLAN-ERROR: fix task-1 Done line\n"))
        self.assertIsNone(steps.PLAN_ERROR_RE.search("see PLAN-ERROR: x in the text\n"))

    def test_plan_error_goes_to_architect_free(self):
        r = self.nxt([dev("a1"), done("a1"), reviewer("a2"), self.fail("a2", "VERDICT: FAIL\nPLAN-ERROR: rename X in task-1 Done\n")])
        self.assertEqual((r["action"], r["agent"], r["role"], r["free"]), ("dispatch", "architect", "architect", True))
        self.assertIn("rename X in task-1 Done", r["prompt"])

    def test_plain_fail_still_goes_to_dev(self):
        r = self.nxt([dev("a1"), done("a1"), reviewer("a2"), self.fail("a2")])
        self.assertEqual(self.who(r), ("dispatch", "dev", "sonnet"))

    def test_after_architect_done_reviewer_free(self):
        evs = [dev("a1"), done("a1"), reviewer("a2"), self.fail("a2", "PLAN-ERROR: x\n"), self.architect("a3")]
        evs.append(ev("report", id="a3", role="architect", verdict="DONE", file="p.md"))
        r = self.nxt(evs)
        self.assertEqual((r["action"], r["agent"], r["free"]), ("dispatch", "reviewer", True))

    def test_after_architect_blocked_asks_lead(self):
        evs = [dev("a1"), done("a1"), reviewer("a2"), self.fail("a2", "PLAN-ERROR: x\n"), self.architect("a3")]
        evs.append(ev("report", id="a3", role="architect", verdict="BLOCKED", file="p.md"))
        self.assertEqual(self.nxt(evs)["action"], "ask_main")

    def test_second_plan_error_asks_lead(self):
        evs = [dev("a1"), done("a1"), reviewer("a2"), self.fail("a2", "PLAN-ERROR: x\n"), self.architect("a3")]
        evs += [ev("report", id="a3", role="architect", verdict="DONE", file="p.md"), reviewer("a4"), self.fail("a4", "PLAN-ERROR: y\n")]
        self.assertEqual(self.nxt(evs)["action"], "ask_main")

    def test_plan_error_not_a_failed_round(self):
        mine = [dev("a1"), done("a1"), reviewer("a2"), self.fail("a2", "PLAN-ERROR: x\n"), reviewer("a4"), self.fail("a4")]
        self.assertEqual(steps._failed_rounds(mine), 1)


if __name__ == "__main__":
    unittest.main()
