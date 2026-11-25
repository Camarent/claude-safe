"""The escalation ladder of pipeline.steps.next_step against rules E1 and E4 (ORCHESTRATOR.md, Model ladder)."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import steps

TASK = {"id": "task-1", "owner_sonnet": False, "review_sonnet": False}


def ev(kind, **kw):
    return {"ev": kind, "task": "task-1", **kw}


def dev_rep(i, verdict, file="d.md"):
    return ev("report", id=i, role="dev", verdict=verdict, file=file, reason="needs a decision")


def rev_rep(i, verdict, file="r.md"):
    return ev("report", id=i, role="reviewer", verdict=verdict, file=file, reason="one defect")


def dispatch(i, role, model="haiku"):
    return ev("dispatch", id=i, agent=role, role=role, model=model)


def script_test_run(i):
    return ev("dispatch", id=i, agent="script", role="script", model=None, why="test run (x)")


class EscalationLadderTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.a = SimpleNamespace(stage="s", dir=self.d)
        Path(self.d, "pipeline.json").write_text(json.dumps({"gate": False, "testrun": False, "qualityrun": False}), encoding="utf-8")

    def nxt(self, mine):
        return steps.next_step(self.a, TASK, mine, "a9")

    def test_e1b_dev_escalate_with_reason_goes_to_sonnet(self):
        r = self.nxt([dispatch("a1", "dev"), dev_rep("a1", "ESCALATE")])
        self.assertEqual((r["agent"], r["model"]), ("dev", "sonnet"))

    def test_e1a_two_red_test_runs_send_the_dev_to_sonnet(self):
        Path(self.d, "pipeline.json").write_text(json.dumps({"gate": False, "testrun": True, "qualityrun": False}), encoding="utf-8")
        Path(self.d, ".work", "s").mkdir(parents=True, exist_ok=True)
        Path(self.d, ".work", "s", "task-1.md").write_text("Predicted paths: README.md\nTest: node --test x\n", encoding="utf-8")
        red = "VERDICT: FAIL\n`node --test x` exited 1:\nnot ok 1 - x\n"
        t1, t2 = Path(self.d, "t1.md"), Path(self.d, "t2.md")
        t1.write_text(red, encoding="utf-8")
        t2.write_text(red, encoding="utf-8")
        one = [dispatch("a1", "dev"), dev_rep("a1", "DONE"), script_test_run("a2"), ev("report", id="a2", role="script", verdict="FAIL", file=str(t1))]
        r = self.nxt(one)
        self.assertEqual((r["agent"], r["model"]), ("dev", "haiku"))  # one red run: the Haiku rework
        two = [*one, dispatch("a3", "dev"), dev_rep("a3", "DONE"), script_test_run("a4"), ev("report", id="a4", role="script", verdict="FAIL", file=str(t2))]
        r = self.nxt(two)
        self.assertEqual((r["agent"], r["model"]), ("dev", "sonnet"))  # E1(a): two Haiku tries, still red

    def test_e4_second_dev_escalate_blocks_to_the_lead_with_reason(self):
        mine = [dispatch("a1", "dev"), dev_rep("a1", "ESCALATE"), dispatch("a2", "dev", "sonnet"), dev_rep("a2", "ESCALATE")]
        r = self.nxt(mine)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("needs a decision", r["message"])

    def test_e1c_reviewer_escalate_goes_to_sonnet_reviewer(self):
        r = self.nxt([dispatch("a1", "dev"), dev_rep("a1", "DONE"), dispatch("a2", "reviewer"), rev_rep("a2", "ESCALATE")])
        self.assertEqual((r["agent"], r["model"]), ("reviewer", "sonnet"))

    def test_e4_second_reviewer_escalate_blocks_to_the_lead_with_reason(self):
        mine = [
            dispatch("a1", "dev"),
            dev_rep("a1", "DONE"),
            dispatch("a2", "reviewer"),
            rev_rep("a2", "ESCALATE"),
            dispatch("a3", "reviewer", "sonnet"),
            rev_rep("a3", "ESCALATE", "r2.md"),
        ]
        r = self.nxt(mine)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("one defect", r["message"])

    def test_e2_first_review_fail_keeps_the_haiku_rework(self):
        mine = [dispatch("a1", "dev"), dev_rep("a1", "DONE"), dispatch("a2", "reviewer"), rev_rep("a2", "FAIL")]
        r = self.nxt(mine)
        self.assertEqual((r["agent"], r["model"]), ("dev", "haiku"))


if __name__ == "__main__":
    unittest.main()
