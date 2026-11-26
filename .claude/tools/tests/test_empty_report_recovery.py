"""A dev BLOCKED with an unclear reason (the auto retry already ran) goes to review when the job has a commit and its Test lines run green."""

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import steps
from pipeline.configuration import BLOCKED, DONE, FAIL, METRICS_LOG
from tests.support.tmpdirs import tracked_mkdtemp

TASK = {"id": "task-1", "owner_sonnet": False, "review_sonnet": False}
DIFF = "diff --git a/x.py b/x.py\n+x = 1\n"


def ev(kind, **kw):
    return {"ev": kind, "task": "task-1", **kw}


class EvidenceTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        Path(self.d, ".work", "s").mkdir(parents=True)
        Path(self.d, "pipeline.json").write_text(
            json.dumps({"worktrees": True, "gate": False, "testrun": True, "qualityrun": False, "prereview": False, "gates": []}),
            encoding="utf-8",
        )
        self.a = SimpleNamespace(stage="s", dir=self.d)
        self.report_no = 0

    def dev_blocked(self, reason):
        self.report_no += 1
        f = Path(self.d, f"r{self.report_no}.md")
        f.write_text(f"VERDICT: BLOCKED\n{reason}\n", encoding="utf-8")
        return [
            ev("dispatch", id="a1", agent="dev", role="dev", model="haiku", why=None),
            ev("report", id="a1", role="dev", verdict=BLOCKED, reason=reason, file=str(f)),
        ]

    def nxt(self, mine, diff, test):
        self.a.orch_events = mine
        with (
            mock.patch.object(steps, "job_diff", return_value=diff),
            mock.patch.object(steps, "_test_run", return_value=test) as run,
        ):
            r = steps.next_step(self.a, TASK, mine, "a9")
        return r, run

    def metrics(self):
        p = Path(self.d, ".work", "s", METRICS_LOG)
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def test_commit_and_green_tests_dispatch_the_reviewer(self):
        r, run = self.nxt(self.dev_blocked("no clear verdict"), DIFF, (DONE, "1 Test line(s) green"))
        self.assertEqual((r["action"], r["role"]), ("dispatch", "reviewer"))
        run.assert_called_once()
        self.assertIn("verdict from evidence: commit + green Test lines", self.metrics())

    def test_no_commit_asks_the_owner(self):
        r, run = self.nxt(self.dev_blocked("no report file written"), "", (DONE, "1 Test line(s) green"))
        self.assertEqual(r["action"], "ask_main")
        run.assert_not_called()
        self.assertNotIn("verdict from evidence", self.metrics())

    def test_red_test_asks_the_owner(self):
        r, _ = self.nxt(self.dev_blocked("no clear verdict"), DIFF, (FAIL, "`pytest` exited 1"))
        self.assertEqual(r["action"], "ask_main")

    def test_skipped_test_run_asks_the_owner(self):
        r, _ = self.nxt(self.dev_blocked("no clear verdict"), DIFF, (DONE, "skipped pytest: identical passed tree, run r1"))
        self.assertEqual(r["action"], "ask_main")

    def test_real_blocked_reason_asks_the_owner_unchanged(self):
        r, run = self.nxt(self.dev_blocked("the brief names a file that does not exist"), DIFF, (DONE, "1 Test line(s) green"))
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("dev verdict BLOCKED (the brief names a file that does not exist)", r["message"])
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
