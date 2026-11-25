"""pipeline.e1: the E1 events a, b, c derived from a task's events, and model_for on top of them."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import e1
from pipeline.configuration import DONE, ESCALATE, EV_DISPATCH, EV_REPORT, FAIL, HAIKU_DEV_TRIES, MODEL_HAIKU, MODEL_SONNET


def dispatch(sid, role, model=MODEL_HAIKU, why=None):
    """Synthetic dispatch event of step SID (the task's events are built from these and report())."""
    return {"ev": EV_DISPATCH, "id": sid, "role": role, "model": model, "why": why}


def report(sid, role, verdict, file=""):
    """Synthetic report event of step SID; FILE is the report text the reviewer PLAN-ERROR check reads."""
    return {"ev": EV_REPORT, "id": sid, "role": role, "verdict": verdict, "file": file}


def red_test_run(sid):
    """One red test run: its script dispatch then its FAIL report."""
    return [dispatch(sid, "script", why="test run: Test lines"), report(sid, "script", FAIL)]


class E1EventTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.plain = self.write("plain.md", "FAIL: the diff misses the test\n")
        self.plan_error = self.write("plan.md", "PLAN-ERROR: the task file names a missing path\n")

    def write(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_no_events_is_none(self):
        self.assertIsNone(e1.e1_event("dev", []))
        self.assertIsNone(e1.e1_event("reviewer", []))

    def test_a_red_gate_reports_reach_haiku_dev_tries(self):
        one_red = red_test_run("t1")
        self.assertIsNone(e1.e1_event("dev", one_red))
        self.assertEqual(e1.e1_event("dev", one_red + red_test_run("t2")), "a")
        self.assertEqual(HAIKU_DEV_TRIES, 2)  # the boundary the test above pins

    def test_a_failed_review_rounds_over_haiku_rework_tries(self):
        rounds = [
            dispatch("r1", "reviewer"),
            report("r1", "reviewer", FAIL, self.plain),
            dispatch("r2", "reviewer"),
            report("r2", "reviewer", FAIL, self.plain),
        ]
        self.assertEqual(e1.e1_event("dev", rounds), "a")
        self.assertIsNone(e1.e1_event("dev", rounds[:2]))

    def test_plan_error_review_is_no_dev_round(self):
        rounds = [
            dispatch("r1", "reviewer"),
            report("r1", "reviewer", FAIL, self.plan_error),
            dispatch("r2", "reviewer"),
            report("r2", "reviewer", FAIL, self.plan_error),
        ]
        self.assertIsNone(e1.e1_event("dev", rounds))

    def test_b_last_dev_report_escalates(self):
        mine = [dispatch("d1", "dev"), report("d1", "dev", ESCALATE, self.plain)]
        self.assertEqual(e1.e1_event("dev", mine), "b")
        self.assertIsNone(e1.e1_event("reviewer", mine))

    def test_b_sticky_sonnet_dev_stays_after_done(self):
        mine = [dispatch("d1", "dev", MODEL_SONNET, why="E1(b): dev escalated; Sonnet dev"), report("d1", "dev", DONE, self.plain)]
        self.assertEqual(e1.e1_event("dev", mine), "b")

    def test_done_dev_without_sonnet_is_none(self):
        mine = [dispatch("d1", "dev"), report("d1", "dev", ESCALATE, self.plain), dispatch("d2", "dev"), report("d2", "dev", DONE, self.plain)]
        self.assertIsNone(e1.e1_event("dev", mine))

    def test_c_reviewer_escalate_report(self):
        mine = [dispatch("r1", "reviewer"), report("r1", "reviewer", ESCALATE, self.plain)]
        self.assertEqual(e1.e1_event("reviewer", mine), "c")
        self.assertIsNone(e1.e1_event("dev", mine))

    def test_model_for_haiku_without_event(self):
        self.assertEqual(e1.model_for("dev", {"owner_sonnet": True}, []), (MODEL_HAIKU, None))
        self.assertEqual(e1.model_for("reviewer", {"review_sonnet": True}, []), (MODEL_HAIKU, None))

    def test_model_for_sonnet_with_e1_why(self):
        model, why = e1.model_for("dev", {"owner_sonnet": False}, red_test_run("t1") + red_test_run("t2"))
        self.assertEqual(model, MODEL_SONNET)
        self.assertTrue((why or "").startswith("E1(a)"))
        mine = [dispatch("r1", "reviewer"), report("r1", "reviewer", ESCALATE, self.plain)]
        model, why = e1.model_for("reviewer", {"review_sonnet": False}, mine)
        self.assertEqual(model, MODEL_SONNET)
        self.assertTrue((why or "").startswith("E1(c)"))


if __name__ == "__main__":
    unittest.main()
