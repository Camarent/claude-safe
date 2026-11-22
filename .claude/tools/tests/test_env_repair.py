"""Environment repair: a red test run or task gates that name a missing environment piece rerun the job worktree's setup once (not a dev try), a second env red goes to the lead, a plain red still goes to the dev ladder."""

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import steps
from pipeline.classify import env_failure
from pipeline.steps_env import ENV_REPAIR_WHY
from tests.support.tmpdirs import tracked_mkdtemp

TASK = {"id": "task-1", "owner_sonnet": False, "review_sonnet": False}
NO_MODULE = "ModuleNotFoundError: No module named 'coverage'\n"


def ev(kind, **kw):
    return {"ev": kind, "task": "task-1", **kw}


class EnvRepairTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        Path(self.d, ".work", "s").mkdir(parents=True)
        self.cfg([])
        self.a = SimpleNamespace(stage="s", dir=self.d)

    def cfg(self, gates):
        base = {"worktrees": True, "gate": False, "testrun": True, "qualityrun": False, "prereview": False, "gates": gates}
        Path(self.d, "pipeline.json").write_text(json.dumps(base), encoding="utf-8")

    def report(self, name, text):
        f = Path(self.d, name)
        f.write_text(text, encoding="utf-8")
        return str(f)

    def nxt(self, mine):
        self.a.orch_events = mine
        return steps.next_step(self.a, TASK, mine, "r9")

    def dev_done(self):
        return [
            ev("dispatch", id="a1", agent="dev", role="dev", model="haiku", why=None),
            ev("report", id="a1", role="dev", verdict="DONE", file=self.report("dev.md", "VERDICT: DONE\n")),
        ]

    def script_run(self, sid, why, verdict, text):
        return [
            ev("dispatch", id=sid, agent="script", role="script", model=None, why=why),
            ev("report", id=sid, role="script", verdict=verdict, file=self.report(f"{sid}.md", text)),
        ]

    def run_test_step(self, sid, verdict, text):
        return self.script_run(sid, "test run (the task's Test lines in its worktree)", verdict, text)

    def repair(self, rid, kind="test run"):
        return self.script_run(rid, f"{ENV_REPAIR_WHY}: {kind}", "DONE", "repair done\n")


class EnvFailureClassTest(unittest.TestCase):
    def test_env_output_is_env_failure(self):
        self.assertTrue(env_failure(NO_MODULE))
        self.assertTrue(env_failure("sh: 1: uv: command not found\n"))
        self.assertTrue(env_failure("no config path given\n"))

    def test_plain_red_is_no_env_failure(self):
        self.assertFalse(env_failure("FAIL test_x.py::test_y - AssertionError: 2 != 3\n"))


class TestRunRepairTest(EnvRepairTest):
    def test_env_red_dispatches_the_repair_not_a_dev(self):
        r = self.nxt(self.dev_done() + self.run_test_step("t1", "FAIL", NO_MODULE))
        self.assertEqual((r["action"], r["role"]), ("dispatch", "script"))
        self.assertTrue(r["why"].startswith(ENV_REPAIR_WHY))
        self.assertIn("--repair-env", r["prompt"])

    def test_after_repair_the_test_run_runs_again(self):
        mine = self.dev_done() + self.run_test_step("t1", "FAIL", NO_MODULE) + self.repair("r1")
        r = self.nxt(mine)
        self.assertEqual(r["role"], "script")
        self.assertTrue(r["why"].startswith("test run"))
        self.assertEqual(r["id"], "r9")

    def test_second_env_red_asks_the_lead(self):
        mine = self.dev_done() + self.run_test_step("t1", "FAIL", NO_MODULE) + self.repair("r1") + self.run_test_step("t2", "FAIL", NO_MODULE)
        r = self.nxt(mine)
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("environment still red after the repair (test run)", r["message"])

    def test_plain_red_still_goes_to_the_dev_ladder(self):
        r = self.nxt(self.dev_done() + self.run_test_step("t1", "FAIL", "FAIL test_x.py::test_y - AssertionError\n"))
        self.assertEqual((r["action"], r["role"]), ("dispatch", "dev"))

    def test_env_red_is_no_dev_try(self):
        mine = self.dev_done() + self.run_test_step("t1", "FAIL", NO_MODULE)
        r = self.nxt(mine)
        self.assertNotEqual(r.get("role"), "dev")
        self.assertEqual(sum(1 for e in mine if e["ev"] == "dispatch" and e["role"] == "dev"), 1)


class TaskGatesRepairTest(EnvRepairTest):
    def setUp(self):
        super().setUp()
        self.cfg([{"name": "lint", "cmd": "true"}])

    def gates_run(self, sid, verdict, text):
        return self.script_run(sid, "task gates: full gate set in the job worktree; FAIL goes back to the dev", verdict, text)

    def test_env_red_gates_dispatch_the_repair(self):
        mine = self.dev_done() + self.run_test_step("t1", "DONE", "VERDICT: DONE\n") + self.gates_run("g1", "FAIL", NO_MODULE)
        r = self.nxt(mine)
        self.assertEqual(r["role"], "script")
        self.assertTrue(r["why"].startswith(ENV_REPAIR_WHY))

    def test_after_repair_the_gates_run_again(self):
        mine = self.dev_done() + self.run_test_step("t1", "DONE", "VERDICT: DONE\n") + self.gates_run("g1", "FAIL", NO_MODULE) + self.repair("r1", "task gates")
        r = self.nxt(mine)
        self.assertEqual(r["role"], "script")
        self.assertTrue(r["why"].startswith("task gates"))


if __name__ == "__main__":
    unittest.main()
