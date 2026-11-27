"""Routing log at dispatch: every Sonnet dispatch carries an E1 letter; the first reviewer FAIL reworks on Haiku; PLAN-ERROR counts no round."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import steps
from pipeline.configuration import ARCHITECT, EV_DISPATCH, MODEL_HAIKU, MODEL_SONNET
from pipeline.e1 import model_for
from pipeline.routing_log import UNLOGGED_WHY, unlogged_routes
from test_task_gates import TASK, _SeqBase, dev, ev, gate, gate_rep


def rep(verdict, role="dev", i="a1", file="d.md"):
    return ev("report", id=i, role=role, verdict=verdict, file=file)


def assert_routes_logged(test, r):
    """The dispatch R names its route: no unlogged Sonnet/Opus route, and a Sonnet route never carries the unlogged why. No guard: always checked."""
    test.assertEqual(unlogged_routes([{"ev": EV_DISPATCH, **r}]), [], r)
    if r["model"] == MODEL_SONNET:
        test.assertNotEqual(r.get("why"), UNLOGGED_WHY, r)
        test.assertTrue(r.get("why"), r)


class UnloggedGuardTest(_SeqBase):
    def test_sonnet_without_letter_is_named_unlogged(self):
        with mock.patch.object(steps, "model_for", return_value=(MODEL_SONNET, None)):
            r = steps._disp(self.a, TASK, "a9", "dev")
        self.assertEqual(r["model"], MODEL_SONNET)
        self.assertEqual(r["why"], UNLOGGED_WHY)

    def test_haiku_without_letter_has_no_why(self):
        r = steps._disp(self.a, TASK, "a9", "dev")
        self.assertEqual(r["model"], "haiku")
        self.assertNotIn("why", r)

    def test_sonnet_with_letter_keeps_its_why(self):
        r = steps._disp(self.a, TASK, "a9", "dev", mine=[dev("a1"), rep("ESCALATE")])
        self.assertEqual(r["model"], MODEL_SONNET)
        self.assertTrue(r["why"].startswith("E1(b)"))


def step_of(test, evs, task=TASK):
    """The next step after the task's events EVS (the whole log in test.a.orch_events, like graph.decide)."""
    test.a.orch_events = evs
    r = steps.next_step(test.a, task, steps.history(evs, task["id"]), "a9")
    assert r is not None, "next_step returned None"
    return r


class FirstFailHaikuTest(_SeqBase):
    def test_first_reviewer_fail_gives_haiku_dev(self):
        r = step_of(self, [dev("a1"), rep("DONE"), rep("FAIL", "reviewer", "a2")])
        self.assertEqual((r["action"], r["role"], r["model"]), ("dispatch", "dev", "haiku"))
        self.assertNotIn("why", r)

    def test_second_reviewer_fail_gives_sonnet_e1a(self):
        evs = [dev("a1"), rep("DONE"), rep("FAIL", "reviewer", "a2"), dev("a3"), rep("DONE", i="a3"), rep("FAIL", "reviewer", "a4")]
        r = step_of(self, evs)
        self.assertEqual((r["role"], r["model"]), ("dev", MODEL_SONNET))
        self.assertTrue(r["why"].startswith("E1(a)"), r["why"])

    def test_plan_error_fail_counts_no_round(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("PLAN-ERROR: the task file names the wrong symbol\n")
        self.addCleanup(os.remove, f.name)
        plan = rep("FAIL", "reviewer", "a2", file=f.name)
        # one real FAIL after a PLAN-ERROR FAIL: one round counted, so the next dev stays on Haiku
        mine = [dev("a1"), rep("DONE"), plan, dev("a3"), rep("DONE", i="a3"), rep("FAIL", "reviewer", "a4")]
        self.assertIsNone(model_for("dev", TASK, mine)[1])
        self.assertEqual(model_for("dev", TASK, mine)[0], "haiku")


class EveryDispatchPathTest(_SeqBase):
    """Walk the dispatch paths: none yields a Sonnet dispatch without an E1 letter."""

    def test_extra_round_after_two_fails(self):
        evs = [dev("a1"), rep("DONE"), rep("FAIL", "reviewer", "a2"), dev("a3"), rep("DONE", i="a3"), rep("FAIL", "reviewer", "a4")]
        r = step_of(self, evs)
        self.assertEqual((r["role"], r["model"]), ("dev", MODEL_SONNET))
        self.assertTrue(r["why"].startswith("E1(a)"), r["why"])
        assert_routes_logged(self, r)

    def test_dirty_rework_path(self):
        self.cfg(prereview=False)  # no local pre-review script in front of the reviewer: the dirty check is next
        with mock.patch.object(steps, "job_dirty", return_value=["x.py"]):
            r = step_of(self, [dev("a1"), rep("DONE"), gate("a2"), gate_rep("a2", "DONE")])  # the task gates are green first
        self.assertEqual((r["action"], r["role"], r["model"]), ("dispatch", "dev", MODEL_HAIKU))
        self.assertNotIn("why", r)
        assert_routes_logged(self, r)

    def test_gates_rework_path(self):
        self.cfg(worktrees=False, gates=[{"name": "lint", "cmd": "true"}])  # no task gates: the Gates: line check runs on the dev report
        r = step_of(self, [dev("a1"), rep("DONE", file="no-such-report.md")])
        self.assertEqual((r["action"], r["role"], r["model"]), ("dispatch", "dev", MODEL_HAIKU))
        self.assertNotIn("why", r)
        assert_routes_logged(self, r)

    def test_plan_fix_path(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write("PLAN-ERROR: the task file names the wrong symbol\n")
        self.addCleanup(os.remove, f.name)
        r = step_of(self, [dev("a1"), rep("DONE"), rep("FAIL", "reviewer", "a2", file=f.name)])
        self.assertEqual((r["action"], r["role"], r["model"]), ("dispatch", ARCHITECT, None))
        assert_routes_logged(self, r)


if __name__ == "__main__":
    unittest.main()
