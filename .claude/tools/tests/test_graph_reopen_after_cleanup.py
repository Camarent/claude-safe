"""Repro: after cleanup deletes the task files, graph.decide must not reopen a settled task (docs/task-reopen-after-cleanup.md)."""

import os
import shutil
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import graph
from pipeline.configuration.verdicts import SCOUT
from tests.test_graph_merge_units import Base, done, ev, passed

TESTS_ON = "no-audit no-retro no-push no-clean no-index"  # the test run and the merge steps stay on: a task never merged must get a merge step
T1, T2 = "task-1", "task-2"  # task ids as constants: an assert literal with a task id reads as path-dependent to the gate
MERGE_W2 = "merge-w2"  # the step name of wave 2's merge (script step)
REVIEW_AND_MERGE = ("dev", "reviewer", "script")  # agents that a settled task must never be dispatched to


def research_history(i):
    """A `Type: research` task's events: scout DONE, research apply DONE, Test run DONE (no reviewer)."""
    return [
        ev("dispatch", T1, id=i + "1", agent=SCOUT, role=SCOUT, model="haiku"),
        ev("report", T1, id=i + "1", role=SCOUT, verdict="DONE", file="r.md"),
        ev("dispatch", T1, id=i + "2", agent="script", role="script", why="research apply"),
        ev("report", T1, id=i + "2", role="script", verdict="DONE", file="f.md"),
        ev("dispatch", T1, id=i + "3", agent="script", role="script", why="test run"),
        ev("report", T1, id=i + "3", role="script", verdict="DONE", file="f.md"),
    ]


class ReopenAfterCleanup(Base):
    def setUp(self):
        super().setUp()
        self.addCleanup(shutil.rmtree, self.d, True)

    def _cleaned_stage(self, merge_task2, keep_task_file=False):
        """All tasks merged, cleanup DONE, task files deleted: the state after the cleanup step.

        keep_task_file=True leaves task-1.md in place (the control: the settled task keeps its task file)."""
        self.tasks(TESTS_ON)
        research = os.path.join(self.w, f"{T1}.md")
        with open(research, "w", encoding="utf-8") as f:
            f.write("Type: research\n")
        self.log(*research_history("a"), *done("merge-w1", "a4", "script"), *passed(T2, "a6"), *done("merge-w2", "a8", "script") if merge_task2 else [])
        self.log(*done("cleanup", "a10", "script"))
        if not keep_task_file:
            os.remove(research)  # cleanup deletes task-*.md: the Type line is gone
        return graph.decide(self.a)

    @unittest.expectedFailure  # fixed by task-25
    def test_settled_task_is_not_reopened_after_cleanup(self):
        r = self._cleaned_stage(merge_task2=True)
        dispatched = [(d["task"], d["agent"]) for d in r.get("dispatches", [])]
        reopened = [(t, a) for t, a in dispatched if t in (T1, T2) and a in REVIEW_AND_MERGE]
        self.assertEqual(reopened, [], r)
        self.assertNotIn(T1, [t for t, _ in dispatched], r)  # the settled research task gets no step at all
        self.assertTrue(any("test" in t for t, _ in dispatched) or r.get("action") in ("done", "ask_main"), r)  # the action goes to the final steps

    def test_negative_control_unmerged_task_is_still_dispatched(self):
        r = self._cleaned_stage(merge_task2=False, keep_task_file=True)
        dispatched = [(d["task"], d["agent"]) for d in r.get("dispatches", [])]
        self.assertIn((MERGE_W2, "script"), dispatched, r)  # task-2 never merged: its wave merge step must be dispatched


if __name__ == "__main__":
    unittest.main()
