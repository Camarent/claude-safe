"""cmd_report reads a dev report from the job worktree when the main report has no VERDICT line; a main report with one is never overwritten."""

import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_orch, worktree
from pipeline.configuration import EV_DISPATCH, ORCH_LOG
from tests.support.tmpdirs import tracked_mkdtemp

STAGE = "s"
TASK = "task-1"
ID = "a1"


class ReportPathTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        Path(self.d, "pipeline.json").write_text(
            json.dumps({"worktrees": True, "gate": False, "testrun": True, "qualityrun": False, "prereview": False, "gates": []}),
            encoding="utf-8",
        )
        self.work = Path(self.d, ".work", STAGE)
        self.work.mkdir(parents=True)
        self.log = self.work / ORCH_LOG
        self.log.write_text(json.dumps({"ev": EV_DISPATCH, "id": ID, "task": TASK, "role": "dev", "agent": "dev"}) + "\n", encoding="utf-8")
        root = worktree.layout(self.d, STAGE, TASK)[2]
        self.wt_report = Path(root, ".work", STAGE, "reports", f"report-{ID}.md")
        self.main_report = self.work / "reports" / f"report-{ID}.md"
        self.a = SimpleNamespace(dir=self.d, stage=STAGE, id=ID, text=None)

    def write_worktree_report(self, body):
        self.wt_report.parent.mkdir(parents=True, exist_ok=True)
        self.wt_report.write_text(body, encoding="utf-8")

    def write_main_report(self, body):
        self.main_report.parent.mkdir(parents=True, exist_ok=True)
        self.main_report.write_text(body, encoding="utf-8")

    def run_report(self):
        with contextlib.redirect_stdout(io.StringIO()):
            cli_orch.cmd_report(self.a)
        reports = [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()]
        return [e for e in reports if e["ev"] == "report"][-1]

    def test_worktree_report_copied_to_main_when_main_missing(self):
        self.write_worktree_report("VERDICT: DONE\nall green\n")
        ev = self.run_report()
        self.assertEqual(ev["verdict"], "DONE")
        self.assertEqual(self.main_report.read_text(encoding="utf-8"), "VERDICT: DONE\nall green\n")

    def test_worktree_report_copied_when_main_has_no_verdict_line(self):
        self.write_main_report("blank-ish notes without a verdict\n")
        self.write_worktree_report("VERDICT: DONE\nall green\n")
        ev = self.run_report()
        self.assertEqual(ev["verdict"], "DONE")
        self.assertEqual(self.main_report.read_text(encoding="utf-8"), "VERDICT: DONE\nall green\n")

    def test_main_report_with_verdict_is_never_overwritten(self):
        self.write_main_report("VERDICT: BLOCKED\nneeds a human\n")
        self.write_worktree_report("VERDICT: DONE\nall green\n")
        ev = self.run_report()
        self.assertEqual(ev["verdict"], "BLOCKED")
        self.assertEqual(self.main_report.read_text(encoding="utf-8"), "VERDICT: BLOCKED\nneeds a human\n")

    def test_no_worktree_report_keeps_old_behaviour(self):
        ev = self.run_report()
        self.assertFalse(self.main_report.exists())
        self.assertEqual(ev["reason"], "no report file written")

    def test_worktree_report_without_verdict_line_is_not_copied(self):
        self.write_worktree_report("just notes, no verdict\n")
        self.run_report()
        self.assertFalse(self.main_report.exists())


if __name__ == "__main__":
    unittest.main()
