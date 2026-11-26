"""PLAN GAP with any separator goes to Sonnet; a lead retry after a dev ESCALATE starts on Sonnet (E1(b) on the pre-retry events, history() hides them)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import steps
from test_task_gates import TASK, _SeqBase, dev, ev


class PlanGapReTest(unittest.TestCase):
    def test_matches_any_separator_prefix_and_indent(self):
        for t in (
            "ESCALATE: PLAN GAP: x",
            "ESCALATE: PLAN GAP - x",
            "ESCALATE: PLAN GAP \u2013 x",
            "ESCALATE: PLAN GAP \u2014 x",
            "VERDICT: ESCALATE: PLAN GAP - x",
            "   ESCALATE: PLAN GAP: x",
        ):
            self.assertTrue(steps.PLAN_GAP_RE.search("head\n" + t + "\n"), t)

    def test_decoys_do_not_match(self):
        for t in ("ESCALATE: planning is hard", "there is a plan gap in the sentence", "see ESCALATE: PLAN GAP: x", "ESCALATE: PLAN GAPS x"):
            self.assertIsNone(steps.PLAN_GAP_RE.search(t + "\n"), t)


class LeadRetryTest(_SeqBase):
    def nxt(self, evs, task=TASK):  # like graph.decide: the whole log in a.orch_events, history() for the step
        self.a.orch_events = evs
        return steps.next_step(self.a, task, steps.history(evs, task["id"]), "a9")

    def rep(self, verdict, role="dev", i="a1"):
        return ev("report", id=i, role=role, verdict=verdict, file="d.md")

    def retry(self, **kw):
        return ev("retry", note="lead", **kw)

    def test_after_dev_escalate_sonnet_with_why(self):
        r = self.nxt([dev("a1"), self.rep("ESCALATE"), self.retry()])
        self.assertEqual(self.who(r), ("dispatch", "dev", "sonnet"))
        self.assertTrue(r["why"].startswith("E1(b)"))  # the E1(b) reason from e1.model_for, read from the events before the retry
        print("dry-run: dev a1 haiku ESCALATE -> lead retry ->", r["action"], r["agent"], r["model"], "|", r["why"])

    def test_after_judge_fail_ask_still_sonnet(self):
        gate = ev("dispatch", id="a2", agent="script", role="script", model=None, why="escalation gate x")
        r = self.nxt([dev("a1"), self.rep("ESCALATE"), gate, self.rep("FAIL", "script", "a2"), self.retry()])
        self.assertEqual(self.who(r), ("dispatch", "dev", "sonnet"))

    def test_two_retries_in_a_row(self):
        r = self.nxt([dev("a1"), self.rep("ESCALATE"), self.retry(), self.retry()])
        self.assertEqual(self.who(r), ("dispatch", "dev", "sonnet"))

    def test_after_reviewer_fail_or_dev_blocked_haiku(self):
        for evs in ([dev("a1"), self.rep("DONE"), self.rep("FAIL", "reviewer", "a2")], [dev("a1"), self.rep("BLOCKED")]):
            r = self.nxt([*evs, self.retry()])
            self.assertEqual(self.who(r), ("dispatch", "dev", "haiku"))
            self.assertIsNone(r.get("why"))  # no E1 event before the retry: no why

    def test_auto_retry_unchanged(self):
        # the auto retry keeps Haiku; a lead retry after a dev ESCALATE with a reason is the E1(b) Sonnet move
        r = self.nxt([dev("a1"), self.rep("ESCALATE"), self.retry(auto=True)])
        self.assertEqual(r.get("model") if r else None, "haiku")
        for kw in ({"from": "script"}, {"from": "review"}):
            r = self.nxt([dev("a1"), self.rep("ESCALATE"), self.retry(**kw)])
            self.assertEqual(r.get("model") if r else None, "sonnet", kw)

    def test_owner_sonnet_task_unchanged(self):
        r = self.nxt([dev("a1", "sonnet"), self.rep("ESCALATE"), self.retry()], {**TASK, "owner_sonnet": True})
        self.assertEqual(self.who(r), ("dispatch", "dev", "sonnet"))

    def test_sonnet_counts_as_a_try(self):
        evs = [dev("a1"), self.rep("ESCALATE"), self.retry(), dev("a3", "sonnet")]
        self.assertEqual(steps._tries(steps.history(evs, TASK["id"]), "dev", "sonnet"), 1)


if __name__ == "__main__":
    unittest.main()
