"""pipeline.e1: the E1 events a, b, c derived from a task's events, and model_for on top of them."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import e1, steps
from pipeline.configuration import DONE, ESCALATE, EV_DISPATCH, EV_REPORT, FAIL, HAIKU_DEV_TRIES, MODEL_HAIKU, MODEL_OPUS, MODEL_SONNET
from test_task_gates import TASK, _SeqBase, dev, done, ev


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


def rep(verdict, role="dev", i="a1"):
    return ev("report", id=i, role=role, verdict=verdict, file="d.md")


def rev(i, model=MODEL_HAIKU):
    return ev("dispatch", id=i, agent="reviewer", role="reviewer", model=model, why=None)


def script(i, why):
    return ev("dispatch", id=i, agent="script", role="script", model=None, why=why)


def red(d, i):
    """A red test-run FAIL report of script step I: its file holds the failing output the classifier reads."""
    path = os.path.join(d, f"{i}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("FAILED (failures=1)\nAssertionError: red\n")
    return ev("report", id=i, role="script", verdict=FAIL, file=path)


class RouteTest(_SeqBase):
    """next_step on real event logs: the retired routes (owner_sonnet, ccn, report task, sticky reviewer) pick no model; E1 does."""

    def nxt(self, mine, task=TASK):
        self.a.orch_events = mine
        return steps.next_step(self.a, task, mine, "a9")

    def test_owner_sonnet_oversized_task_starts_haiku(self):
        r = self.nxt([], {**TASK, "owner_sonnet": True})
        self.assertEqual((r["agent"], r["model"]), ("dev", MODEL_HAIKU))
        self.assertNotIn("why", r)

    def test_ccn_hit_reviewer_fail_then_next_reviewer_is_haiku(self):
        # a dev DONE runs the script steps first, so the reviewer dispatch is asked for directly (no ccn gate, no why)
        mine = [dev("a1"), done("a1"), rev("a2"), rep("FAIL", "reviewer", "a2"), dev("a3"), done("a3")]
        r = steps._disp_reviewer(self.a, TASK, "a4", mine)
        self.assertEqual((r["agent"], r["model"]), ("reviewer", MODEL_HAIKU))
        self.assertNotIn("why", r)

    def test_sonnet_reviewer_without_escalate_does_not_stick(self):
        mine = [dev("a1"), done("a1"), rev("a2", MODEL_SONNET), rep("FAIL", "reviewer", "a2"), dev("a3"), done("a3")]
        r = steps._disp_reviewer(self.a, TASK, "a4", mine)
        self.assertEqual((r["agent"], r["model"]), ("reviewer", MODEL_HAIKU))

    def test_e1a_red_gates_send_the_dev_to_sonnet(self):
        mine = [dev("a1"), done("a1"), script("a2", "test run: x"), red(self.d, "a2"), dev("a3"), done("a3"), script("a4", "test run: x"), red(self.d, "a4")]
        r = self.nxt(mine)
        self.assertEqual((r["agent"], r["model"]), ("dev", MODEL_SONNET))
        self.assertTrue(r["why"].startswith("E1(a)"))

    def test_e1b_dev_escalate_sends_the_dev_to_sonnet(self):
        r = self.nxt([dev("a1"), rep("ESCALATE", "dev", "a1")])
        self.assertEqual((r["agent"], r["model"]), ("dev", MODEL_SONNET))
        self.assertTrue(r["why"].startswith("E1(b)"))

    def test_e1c_reviewer_escalate_sends_the_reviewer_to_sonnet(self):
        r = self.nxt([dev("a1"), done("a1"), rev("a2"), rep("ESCALATE", "reviewer", "a2")])
        self.assertEqual((r["agent"], r["model"]), ("reviewer", MODEL_SONNET))
        self.assertTrue(r["why"].startswith("E1(c)"))


class ReplayTest(_SeqBase):
    """Every Sonnet or Opus dev/reviewer dispatch in a log carries its E1 why; the E1 events each dispatch Sonnet."""

    nxt = RouteTest.nxt

    def check(self, mine, task=TASK):
        r = self.nxt(mine, task)
        if r and r.get("action") == "dispatch" and r["agent"] in ("dev", "reviewer") and r["model"] in (MODEL_SONNET, MODEL_OPUS):
            self.assertTrue((r.get("why") or "").startswith("E1("), mine)
        return r

    def test_no_sonnet_or_opus_dispatch_without_an_e1_why(self):
        logs = [
            [],
            [dev("a1"), done("a1")],
            [dev("a1"), rep("ESCALATE", "dev", "a1"), dev("a2", MODEL_SONNET), done("a2")],
            [dev("a1"), done("a1"), rev("a2"), rep("ESCALATE", "reviewer", "a2"), rev("a3", MODEL_SONNET), rep("FAIL", "reviewer", "a3")],
            [dev("a1"), done("a1"), rev("a2"), rep("FAIL", "reviewer", "a2"), dev("a3"), done("a3"), rev("a4"), rep("FAIL", "reviewer", "a4")],
            [
                dev("a1"),
                done("a1"),
                rev("a2"),
                rep("FAIL", "reviewer", "a2"),
                dev("a3"),
                done("a3"),
                rev("a4"),
                rep("FAIL", "reviewer", "a4"),
                dev("a5", MODEL_SONNET),
                done("a5"),
                rev("a6"),
                rep("FAIL", "reviewer", "a6"),
            ],
            [dev("a1"), rep("BLOCKED", "dev", "a1")],
        ]
        for mine in logs:
            self.check(mine)
        self.check([], {**TASK, "owner_sonnet": True})
        self.check([dev("a1"), done("a1"), rev("a2"), rep("FAIL", "reviewer", "a2")], {**TASK, "review_sonnet": True})

    def test_e1_events_each_dispatch_sonnet_with_their_why(self):
        self.assertEqual(self.check([dev("a1"), rep("ESCALATE", "dev", "a1")])["why"][:5], "E1(b)")
        self.assertEqual(self.check([dev("a1"), done("a1"), rev("a2"), rep("ESCALATE", "reviewer", "a2")])["why"][:5], "E1(c)")
        reds = [dev("a1"), done("a1"), script("a2", "test run: x"), red(self.d, "a2"), dev("a3"), done("a3"), script("a4", "test run: x"), red(self.d, "a4")]
        self.assertEqual(self.check(reds)["why"][:5], "E1(a)")


if __name__ == "__main__":
    unittest.main()
