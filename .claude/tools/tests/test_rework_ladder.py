"""Rework ladder: the first reviewer FAIL reworks on Haiku, Sonnet only after that rework failed review too; a reviewer ESCALATE moves the next reviewer to Sonnet (E1)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import steps
from pipeline.configuration import MODEL_HAIKU, MODEL_SONNET
from test_task_gates import TASK, _SeqBase, dev, ev


def rep(verdict, role="dev", i="a1"):
    return ev("report", id=i, role=role, verdict=verdict, file="d.md")


def reviewer(i, why=None, model=MODEL_HAIKU):
    return ev("dispatch", id=i, agent="reviewer", role="reviewer", model=model, why=why)


class ReworkLadderTest(_SeqBase):
    def nxt(self, mine, task=TASK):
        self.a.orch_events = mine
        return steps.next_step(self.a, task, mine, "a9")

    def test_first_reviewer_fail_reworks_on_haiku(self):
        r = self.nxt([dev("a1"), rep("DONE"), reviewer("a2"), rep("FAIL", "reviewer", "a2")])
        self.assertEqual(self.who(r), ("dispatch", "dev", MODEL_HAIKU))

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
        self.assertEqual(self.who(r), ("dispatch", "dev", MODEL_SONNET))

    def test_reviewer_without_events_is_haiku_no_ccn_brief(self):
        # the ccn gate is gone from the reviewer dispatch: no event, no why, no brief
        d = steps._disp_reviewer(self.a, TASK, "a2", [dev("a1"), rep("DONE")])
        self.assertEqual(self.who(d), ("dispatch", "reviewer", MODEL_HAIKU))
        self.assertNotIn("why", d)
        self.assertFalse(
            d["prompt"].endswith(
                " Complexity hit: check the changed functions named in your reason for the logic a long branch chain can hide (missed cases, wrong fallthrough)."
            )
        )

    def test_reviewer_escalate_moves_next_reviewer_to_sonnet(self):
        mine = [dev("a1"), rep("DONE"), reviewer("a2"), rep("ESCALATE", "reviewer", "a2")]
        r = self.nxt(mine)
        self.assertEqual(self.who(r), ("dispatch", "reviewer", MODEL_SONNET))
        self.assertTrue(r["why"].startswith("E1(c)"))

    def test_one_reviewer_fail_keeps_next_reviewer_on_haiku(self):
        mine = [dev("a1"), rep("DONE"), reviewer("a2"), rep("FAIL", "reviewer", "a2"), dev("a3"), rep("DONE", "dev", "a3")]
        r = steps._disp_reviewer(self.a, TASK, "a4", mine)  # a dev DONE runs the script steps first: ask the reviewer dispatch directly
        self.assertEqual(self.who(r), ("dispatch", "reviewer", MODEL_HAIKU))
        self.assertNotIn("why", r)


if __name__ == "__main__":
    unittest.main()
