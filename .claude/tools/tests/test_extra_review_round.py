"""Extra dev round after the last failed review round: one dev round with the verbatim text of every failed review, then the lead."""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import steps
from pipeline.configuration import EXTRA_ROUND_WHY, MODEL_HAIKU, MODEL_SONNET
from test_task_gates import TASK, _SeqBase, dev, done, ev


def rev_dispatch(i):
    return ev("dispatch", id=i, agent="reviewer", role="reviewer", model="haiku", why=None, free=False)


def extra(i):
    return ev("dispatch", id=i, agent="dev", role="dev", model=MODEL_SONNET, why=EXTRA_ROUND_WHY, free=False)


class ExtraReviewRoundTest(_SeqBase):
    def review_fail(self, i, text):
        """A reviewer FAIL report event whose findings file holds TEXT."""
        f = Path(self.d, f"{i}.md")
        f.write_text(text, encoding="utf-8")
        return ev("report", id=i, role="reviewer", verdict="FAIL", file=str(f))

    def nxt(self, mine, task=TASK):
        self.a.orch_events = mine
        s = steps._TaskState(self.a, task, mine)
        return steps._after_reviewer(self.a, task, mine, "a9", s, mine[-1])

    def rework_text(self):
        files = sorted(Path(self.d).rglob("reviews-all-task-1-a9.md"))
        self.assertEqual(len(files), 1)
        return files[0].read_text(encoding="utf-8")

    def two_fails(self, model2=MODEL_SONNET):
        return [
            dev("d1"),
            done("d1"),
            rev_dispatch("r1"),
            self.review_fail("r1", "FIRST FINDING text\n"),
            dev("d2", model=model2),
            done("d2"),
            rev_dispatch("r2"),
            self.review_fail("r2", "SECOND FINDING text\n"),
        ]

    def test_first_fail_is_a_normal_dev_round(self):
        mine = [dev("d1"), done("d1"), rev_dispatch("r1"), self.review_fail("r1", "one\n")]
        r = self.nxt(mine)
        self.assertEqual((r["action"], r["role"], r["model"]), ("dispatch", "dev", MODEL_HAIKU))  # first FAIL: Haiku rework
        self.assertNotEqual(r.get("why"), EXTRA_ROUND_WHY)

    def test_second_fail_dispatches_extra_round_with_both_findings_verbatim(self):
        r = self.nxt(self.two_fails())
        self.assertEqual((r["action"], r["role"], r["model"]), ("dispatch", "dev", MODEL_SONNET))
        self.assertIn(EXTRA_ROUND_WHY, r["why"])
        text = self.rework_text()
        self.assertIn("FIRST FINDING text", text)
        self.assertIn("SECOND FINDING text", text)

    def test_next_fail_after_the_extra_round_goes_to_the_lead(self):
        mine = [*self.two_fails(), extra("d3"), done("d3"), rev_dispatch("r3"), self.review_fail("r3", "three\n")]
        r = self.nxt(mine)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("review failed 3 rounds", r["message"])

    def test_setting_zero_restores_ask_main_at_second_fail(self):
        with mock.patch.object(steps, "REVIEW_EXTRA_ROUNDS", 0):
            r = self.nxt(self.two_fails())
        self.assertEqual(r["action"], "ask_main")

    def sonnet_twice(self):
        return [
            dev("d1", model=MODEL_SONNET),
            done("d1"),
            rev_dispatch("r1"),
            self.review_fail("r1", "one\n"),
            dev("d2", model=MODEL_SONNET),
            done("d2"),
            rev_dispatch("r2"),
            self.review_fail("r2", "two\n"),
        ]

    def test_extra_round_is_sonnet_never_opus_after_several_sonnet_devs(self):
        r = self.nxt(self.sonnet_twice())  # two Sonnet devs: the Opus extra-round route is gone, the model is e1.model_for's
        self.assertEqual((r["role"], r["model"]), ("dev", MODEL_SONNET))
        self.assertTrue(r["why"].startswith("E1("))
        self.assertIn(EXTRA_ROUND_WHY, r["why"])  # the extra round stays counted by its reason


if __name__ == "__main__":
    unittest.main()
