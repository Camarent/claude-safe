"""A failing final-test whose stage head moved since the run is rerun once by the driver before any fix round."""

import os
import sys
import unittest
from types import SimpleNamespace

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import phases
from pipeline.configuration import FINAL_TEST_RERUN_STEP
from tests.support.gitrepo import git
from tests.support.tmpdirs import tracked_mkdtemp

TRIAGE_FAIL = [
    {"ev": "dispatch", "task": "final-test-triage"},
    {"ev": "report", "task": "final-test-triage", "verdict": "FAIL", "file": "/r/tr.md"},
]  # the triage ran and found red on the stage head only: the fix plan follows


class FinalTestRerunTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        git(self.d, "init", "-q")
        self.a = SimpleNamespace(stage="s", dir=self.d, feature=None)
        self.cfg = {"gates": [{"name": "g", "cmd": "true"}]}
        self.branch = phases._wb(self.a, self.cfg)
        git(self.d, "commit", "-q", "--allow-empty", "-m", "one")
        git(self.d, "branch", "-f", self.branch)
        self.tasks = [{"id": "task-1", "wave": 1}]

    def head(self):
        return git(self.d, "rev-parse", self.branch)

    def ids(self, evs):
        return [s["id"] for g in phases._test_phases(self.a, self.cfg, {"ui": False}, self.tasks, evs) for s in g]

    def evs(self, head):
        return [
            {"ev": "dispatch", "task": "final-test", "head": head},
            {"ev": "report", "task": "final-test", "verdict": "FAIL", "file": "/r/ft.md"},
        ]

    def test_step_carries_the_stage_head(self):
        st = phases._with_head(self.a, self.cfg, phases._final_test_step(self.a, self.cfg, "task-1", "final-test", {"DONE"}, {"PASS"}))
        self.assertEqual(st["head"], self.head())

    def test_moved_head_reruns_before_fix(self):
        self.assertEqual(self.ids(self.evs("0" * 40)), ["final-test", FINAL_TEST_RERUN_STEP])

    def test_same_head_goes_to_fix(self):
        self.assertEqual(self.ids([*self.evs(self.head()), *TRIAGE_FAIL]), ["final-test", "final-test-triage", "fix-plan-test", "final-test-2"])

    def test_no_recorded_head_goes_to_fix(self):
        self.assertEqual(self.ids([*self.evs(""), *TRIAGE_FAIL]), ["final-test", "final-test-triage", "fix-plan-test", "final-test-2"])

    def test_rerun_fail_goes_to_fix(self):
        evs = [
            *self.evs("0" * 40),
            {"ev": "dispatch", "task": FINAL_TEST_RERUN_STEP, "head": self.head()},
            {"ev": "report", "task": FINAL_TEST_RERUN_STEP, "verdict": "FAIL", "file": "/r/rr.md"},
            *TRIAGE_FAIL,
        ]
        self.assertEqual(self.ids(evs), ["final-test", FINAL_TEST_RERUN_STEP, "final-test-triage", "fix-plan-test", "final-test-2"])

    def test_rerun_pass_skips_fix(self):
        evs = [
            *self.evs("0" * 40),
            {"ev": "dispatch", "task": FINAL_TEST_RERUN_STEP, "head": self.head()},
            {"ev": "report", "task": FINAL_TEST_RERUN_STEP, "verdict": "DONE", "file": "/r/rr.md"},
        ]
        self.assertEqual(self.ids(evs), ["final-test", FINAL_TEST_RERUN_STEP])


if __name__ == "__main__":
    unittest.main()
