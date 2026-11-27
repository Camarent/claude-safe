"""A skipped local pre-review is no review round: it is no FAIL of the paid review, and a task whose local gates were all skipped reaches the paid reviewer with 0 rounds."""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import steps
from pipeline.configuration import SKIPPED
from pipeline.steps_history import _failed_rounds
from test_task_gates import TASK, _SeqBase, dev, done, ev

PREREVIEW_WHY = "local pre-review (lp review); FAIL goes back to the dev"


class SkippedPrereviewRoundTest(_SeqBase):
    def prereview(self, i):
        return ev("dispatch", id=i, agent="script", role="script", model=None, why=PREREVIEW_WHY)

    def prereview_report(self, i, verdict, note):
        f = Path(self.d, f"{i}.md")
        f.write_text(f"VERDICT: {verdict}\n{note}\n", encoding="utf-8")
        return ev("report", id=i, role="script", verdict=verdict, reason=note, file=str(f))

    def reviewer_fail(self, i):
        f = Path(self.d, f"{i}.md")
        f.write_text("FAIL bad | f.py:3 x\n", encoding="utf-8")
        return ev("report", id=i, role="reviewer", verdict="FAIL", file=str(f))

    def test_skipped_prereview_is_no_round(self):
        note = "local review skipped: no diff (WARNING: check the review input)"
        mine = [dev("d1"), done("d1"), self.prereview("p1"), self.prereview_report("p1", SKIPPED, note)]
        self.assertEqual(_failed_rounds(mine), 0)

    def test_real_reviewer_fail_still_counts(self):
        mine = [dev("d1"), done("d1"), self.prereview("p1"), self.prereview_report("p1", SKIPPED, "local review skipped: no diff"), self.reviewer_fail("r1")]
        self.assertEqual(_failed_rounds(mine), 1)

    def test_only_skipped_local_gates_reach_the_paid_reviewer_with_zero_rounds(self):
        note = "local review skipped: diff too big (cut at 40000 chars), not counted as FAIL"
        mine = [dev("d1"), done("d1"), self.prereview("p1"), self.prereview_report("p1", SKIPPED, note)]
        self.assertEqual(steps._TaskState(self.a, TASK, mine).rounds, 0)
        r = self.nxt(mine)
        assert r is not None
        self.assertEqual((r["action"], r["role"]), ("dispatch", "reviewer"))
