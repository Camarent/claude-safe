"""pipeline.graph: a wave's merge step skipped (`orch.py skip`) or already in the work branch is not dispatched again, late batches included."""

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import graph, pr_mode
from tests.support.tmpdirs import tracked_mkdtemp

TASKS = """| task | what | owner | price | review |
|---|---|---|---|---|
| task-1 | a | dev | 1 | wave W1, haiku |
| task-2 | b | dev | 1 | wave W2, haiku |

## Graph
wave 1: task-1
wave 2: task-2
flags: no-test no-audit no-retro no-push no-clean no-index
PRE-APPROVED: master merge
"""


def ev(kind, task, **kw):
    return {"ev": kind, "task": task, **kw}


def done(task, i, role="dev", verdict="DONE", file="f.md"):
    return [ev("dispatch", task, id=i, agent=role, role=role, model="haiku"), ev("report", task, id=i, role=role, verdict=verdict, file=file)]


def passed(task, i):
    return done(task, i) + done(task, i + "r", "reviewer", "PASS")


class Base(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.w = Path(self.d, ".work", "s")
        self.w.mkdir(parents=True)
        self.a = SimpleNamespace(stage="s", dir=self.d)
        for name, val in (("has_origin", True), ("pr_active", False), ("gh_down", False), ("direct_flow", True), ("sweep_active", False)):
            pa = mock.patch.object(pr_mode, name, return_value=val)
            pa.start()
            self.addCleanup(pa.stop)
        Path(self.d, "pipeline.json").write_text(
            json.dumps({"gate": False, "testrun": False, "qualityrun": False, "worktrees": True, "docs_cite_final": False}), encoding="utf-8"
        )
        (self.w / "TASKS.md").write_text(TASKS, encoding="utf-8")
        self.evs = []
        self.patch(graph, "_has_job_branches", return_value=True)
        self.patch(graph, "job_info", return_value={"branch": "job/t", "repos": {"main": "/p"}})
        self.patch(graph, "_empty_job_removed", return_value=False)  # the job branch of task-1 is still there: never merged
        self.patch(graph, "_already_merged", return_value=False)
        self.patch(graph, "run_index", return_value=0)

    def patch(self, *a, **kw):
        p = mock.patch.object(*a, **kw)
        m = p.start()
        self.addCleanup(p.stop)
        return m

    def log(self, *events):
        self.evs.extend(events)
        (self.w / "orch.jsonl").write_text("".join(json.dumps(e) + "\n" for e in self.evs), encoding="utf-8")

    def first(self):
        return graph.decide(self.a)["dispatches"][0]


class SkippedMerge(Base):
    def test_skipped_wave_merge_is_not_dispatched_and_no_late_merge(self):
        self.log(*passed("task-1", "a1"), ev("skip", "merge-w1"))
        d = self.first()
        self.assertNotIn("merge", d["task"])
        self.assertEqual(d["agent"], "dev")

    def test_skip_after_failed_merge_closes_the_merge_fix_too(self):
        self.log(*passed("task-1", "a1"), *done("merge-w1", "a3", "script", "FAIL", "m1.md"), ev("skip", "merge-w1"))
        d = self.first()
        self.assertNotIn("merge", d["task"])
        self.assertEqual(d["agent"], "dev")

    def test_unskipped_wave_merge_is_still_dispatched(self):
        self.log(*passed("task-1", "a1"))
        d = self.first()
        self.assertEqual((d["task"], d["agent"]), ("merge-w1", "script"))


class AlreadyMerged(Base):
    def test_branch_content_already_in_work_branch_is_not_dispatched(self):
        self.patch(graph, "_already_merged", return_value=True)
        self.patch(graph, "_unmerged_done", return_value=[])
        self.log(*passed("task-1", "a1"))
        d = self.first()
        self.assertNotIn("merge", d["task"])
        self.assertEqual(d["agent"], "dev")

    def test_unmerged_branch_still_dispatches_the_merge(self):
        self.log(*passed("task-1", "a1"))
        self.assertEqual(self.first()["task"], "merge-w1")


if __name__ == "__main__":
    unittest.main()
