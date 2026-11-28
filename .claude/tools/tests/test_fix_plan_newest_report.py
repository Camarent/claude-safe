"""The fix-plan-test brief names the newest final-test report (the last one in the event log), not an earlier one."""

import os
import sys
import unittest
from types import SimpleNamespace

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import phases
from tests.support.gitrepo import git
from tests.support.tmpdirs import tracked_mkdtemp

TRIAGE_FAIL = [
    {"ev": "dispatch", "task": "final-test-triage"},
    {"ev": "report", "task": "final-test-triage", "verdict": "FAIL", "file": "/r/tr.md"},
]  # the triage ran and found red on the stage head only: the fix plan follows


class FixPlanNewestReportTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        git(self.d, "init", "-q")
        self.a = SimpleNamespace(stage="s", dir=self.d, feature=None)
        self.cfg = {"gates": [{"name": "g", "cmd": "true"}]}
        self.branch = phases._wb(self.a, self.cfg)
        git(self.d, "commit", "-q", "--allow-empty", "-m", "one")
        git(self.d, "branch", "-f", self.branch)
        self.tasks = [{"id": "task-1", "wave": 1}]

    def fix_prompt(self, evs):
        groups = list(phases._test_phases(self.a, self.cfg, {"ui": False}, self.tasks, evs))
        steps = [s for g in groups for s in g if s["id"] == "fix-plan-test"]
        self.assertEqual(len(steps), 1)
        return steps[0]["prompt"]

    def two_reports(self):
        return [
            {"ev": "dispatch", "task": "final-test", "id": "a83"},
            {"ev": "report", "task": "final-test", "verdict": "FAIL", "file": "/r/a83.md", "id": "a83"},
            {"ev": "dispatch", "task": "final-test", "id": "a90"},
            {"ev": "report", "task": "final-test", "verdict": "FAIL", "file": "/r/a90.md", "id": "a90"},
            *TRIAGE_FAIL,
        ]

    def test_brief_cites_newest_report(self):
        prompt = self.fix_prompt(self.two_reports())
        self.assertIn("/r/a90.md", prompt)
        self.assertNotIn("/r/a83.md", prompt)

    def test_single_report_is_cited(self):
        evs = [
            {"ev": "dispatch", "task": "final-test", "id": "a90"},
            {"ev": "report", "task": "final-test", "verdict": "FAIL", "file": "/r/a90.md", "id": "a90"},
            *TRIAGE_FAIL,
        ]
        prompt = self.fix_prompt(evs)
        self.assertIn("Reports: /r/a90.md.", prompt)


if __name__ == "__main__":
    unittest.main()
