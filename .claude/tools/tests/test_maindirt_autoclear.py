"""A BLOCKED run that named main-checkout dirt: the driver reruns the step by itself once the dirt is gone, and asks the lead while it stays."""

import json
import os
import sys
import unittest
import unittest.mock
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import maincheck, steps
from pipeline.configuration import BLOCKED, EV_MAIN_DIRT_CLEARED, MAIN_WRITE_NOTE
from tests.support.tmpdirs import tracked_mkdtemp

NOTE = MAIN_WRITE_NOTE.format(files="f (mtime x, job start y)", more=0)


class MainDirtAutoClearTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        os.makedirs(os.path.join(self.d, ".work", "s"))
        self.a = SimpleNamespace(stage="s", dir=self.d, orch_events=[])
        self.task = {"id": "t1", "owner_sonnet": False}
        self.log = os.path.join(self.d, ".work", "s", "orch.jsonl")

    def blocked(self, text):
        path = os.path.join(self.d, "run.md")
        with open(path, "w") as f:
            f.write(f"VERDICT: BLOCKED\n{text}\n")
        return {"id": "r1", "verdict": BLOCKED, "file": path, "task": "t1"}

    def events(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_autoclear_reruns_the_test_run(self):
        last = self.blocked(NOTE)
        with unittest.mock.patch.object(maincheck, "check", return_value=""):
            d = steps._after_test(self.a, self.task, [], "r2", None, last)
        self.assertEqual(d["agent"], "script")
        self.assertIn("testrun.py", d["prompt"])
        self.assertNotEqual(d["action"], "ask_main")
        cleared = [e for e in self.events() if e["ev"] == EV_MAIN_DIRT_CLEARED]
        self.assertEqual([(e["task"], e["step"]) for e in cleared], [("t1", "r1")])

    def test_autoclear_reruns_the_task_gates(self):
        last = self.blocked(NOTE)
        with unittest.mock.patch.object(maincheck, "check", return_value=""):
            d = steps._after_taskgates(self.a, self.task, [], "r2", None, last)
        self.assertEqual(d["agent"], "script")
        self.assertIn("taskgates.py", d["prompt"])

    def test_dirt_stays_asks_main(self):
        last = self.blocked(NOTE)
        with unittest.mock.patch.object(maincheck, "check", return_value=NOTE):
            d = steps._after_test(self.a, self.task, [], "r2", None, last)
        self.assertEqual(d["action"], "ask_main")
        self.assertNotIn(d.get("agent"), ("dev", "script"))
        self.assertEqual(self.events(), [])

    def test_other_blocked_unchanged(self):
        last = self.blocked("Test lines cannot run: no tool")
        with unittest.mock.patch.object(maincheck, "check", return_value="") as check:
            d = steps._after_test(self.a, self.task, [], "r2", None, last)
        self.assertEqual(d["action"], "ask_main")
        check.assert_not_called()
        self.assertEqual(self.events(), [])


if __name__ == "__main__":
    unittest.main()
