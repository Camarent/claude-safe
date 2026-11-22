"""Rework ladder: the first reviewer FAIL reworks on Haiku, Sonnet only after that rework failed review too; a review_ccn hit starts a Haiku reviewer."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import steps
from pipeline.ccn import CCN_REVIEW_WHY
from test_task_gates import TASK, _SeqBase, dev, ev


def rep(verdict, role="dev", i="a1"):
    return ev("report", id=i, role=role, verdict=verdict, file="d.md")


def reviewer(i, why=None, model=steps.MODEL_HAIKU):
    return ev("dispatch", id=i, agent="reviewer", role="reviewer", model=model, why=why)


class ReworkLadderTest(_SeqBase):
    def nxt(self, mine, task=TASK):
        self.a.orch_events = mine
        return steps.next_step(self.a, task, mine, "a9")

    def test_first_reviewer_fail_reworks_on_haiku(self):
        r = self.nxt([dev("a1"), rep("DONE"), reviewer("a2"), rep("FAIL", "reviewer", "a2")])
        self.assertEqual(self.who(r), ("dispatch", "dev", steps.MODEL_HAIKU))

    def test_second_fail_after_haiku_rework_goes_sonnet(self):
        mine = [
            dev("a1"),
            rep("DONE"),
            reviewer("a2"),
            rep("FAIL", "reviewer", "a2"),
            dev("a3"),
            rep("DONE", "dev", "a3"),
            reviewer("a4"),
            rep("FAIL", "reviewer", "a4"),
        ]
        r = self.nxt(mine)
        self.assertEqual(self.who(r), ("dispatch", "dev", steps.MODEL_SONNET))

    def test_ccn_hit_starts_haiku_reviewer_with_brief(self):
        s = steps._TaskState(self.a, TASK, [dev("a1"), rep("DONE")])
        with mock.patch.object(steps, "review_ccn_hit", return_value=CCN_REVIEW_WHY + ": ccn 12 in f (g), above review_ccn 10"):
            d = steps._disp_reviewer_model(self.a, TASK, "a2", s)
        self.assertEqual(self.who(d), ("dispatch", "reviewer", steps.MODEL_HAIKU))
        self.assertTrue(d["why"].startswith(CCN_REVIEW_WHY))
        self.assertTrue(d["prompt"].endswith(steps.CCN_REVIEW_BRIEF))

    def test_no_ccn_hit_is_haiku_without_brief(self):
        s = steps._TaskState(self.a, TASK, [dev("a1"), rep("DONE")])
        with mock.patch.object(steps, "review_ccn_hit", return_value=None):
            d = steps._disp_reviewer_model(self.a, TASK, "a2", s)
        self.assertEqual(self.who(d), ("dispatch", "reviewer", steps.MODEL_HAIKU))
        self.assertNotIn("why", d)
        self.assertFalse(d["prompt"].endswith(steps.CCN_REVIEW_BRIEF))

    def test_ccn_haiku_fail_moves_next_reviewer_to_sonnet(self):
        mine = [dev("a1"), rep("DONE"), reviewer("a2", why=CCN_REVIEW_WHY + ": x"), rep("FAIL", "reviewer", "a2")]
        self.assertTrue(steps._TaskState(self.a, TASK, mine).reviewer_sonnet)

    def test_plain_haiku_fail_keeps_next_reviewer_on_haiku(self):
        mine = [dev("a1"), rep("DONE"), reviewer("a2"), rep("FAIL", "reviewer", "a2")]
        self.assertFalse(steps._TaskState(self.a, TASK, mine).reviewer_sonnet)


if __name__ == "__main__":
    unittest.main()
