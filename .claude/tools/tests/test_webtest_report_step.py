"""The driver compares the web-tester report's `HEAD:` line with the branch HEAD: a stale or missing HEAD line is BLOCKED (ask_main, reason on line 1), a matching one continues."""

import json
import os
import subprocess
import sys
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

os.environ["PIPELINE_NO_LEADS"] = "1"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import steps
from pipeline.configuration import EV_DISPATCH, EV_REPORT, PASS, WEB_TESTER
from tests.support.tmpdirs import tracked_mkdtemp

GIT_ID = ["-c", "user.name=test", "-c", "user.email=test@example.invalid"]
OLD_SHA = "abc1234" + "0" * 33
TASK = {"id": "task-1", "owner_sonnet": False, "review_sonnet": False}


def _git(repo, *args):
    return subprocess.run(["git", *GIT_ID, "-C", repo, *args], check=True, capture_output=True, text=True).stdout.strip()


class WebtestReportStepTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        _git(self.d, "init", "-q")
        Path(self.d, "f.txt").write_text("x\n", encoding="utf-8")
        _git(self.d, "add", "f.txt")
        _git(self.d, "commit", "-q", "-m", "init")
        self.head = _git(self.d, "rev-parse", "HEAD")
        Path(self.d, "pipeline.json").write_text(json.dumps({"worktrees": True, "gate": False, "testrun": False, "gates": []}), encoding="utf-8")
        Path(self.d, ".work", "s").mkdir(parents=True)
        Path(self.d, ".work", "s", "task-1.md").write_text("Type: webtest\n", encoding="utf-8")
        self.a = SimpleNamespace(stage="s", dir=self.d)

    def report(self, sha_line):
        f = Path(self.d, "web.md")
        f.write_text(f"RESULT: PASS\n{sha_line}\nbody\n", encoding="utf-8")
        return str(f)

    def web_run(self, sha_line):
        f = self.report(sha_line)
        return [
            {"ev": EV_DISPATCH, "task": "task-1", "id": "a1", "agent": WEB_TESTER, "role": WEB_TESTER, "t": 1},
            {"ev": EV_REPORT, "task": "task-1", "id": "a1", "role": WEB_TESTER, "verdict": PASS, "file": f, "t": 2},
        ]

    def test_stale_head_blocks_with_reason_on_line_one(self):
        mine = self.web_run(f"HEAD: {OLD_SHA}")
        self.a.orch_events = mine
        r = steps.next_step(self.a, TASK, mine, "a9")
        assert r is not None  # the test asserts a step came back; narrows the Optional for the lines below
        self.assertEqual(r["action"], "ask_main")
        self.assertTrue(r["message"].startswith(f"stale webtest: report HEAD {OLD_SHA}, branch HEAD {self.head}"), r["message"])
        self.assertNotIn("Test lines", r["message"])

    def test_missing_head_line_blocks(self):
        mine = self.web_run("no head here")
        self.a.orch_events = mine
        r = steps.next_step(self.a, TASK, mine, "a9")
        assert r is not None  # the test asserts a step came back; narrows the Optional for the lines below
        self.assertEqual(r["action"], "ask_main")
        self.assertTrue(r["message"].startswith("stale webtest: no HEAD line"), r["message"])

    def test_dry_walk_missing_head_line_continues(self):
        mine = self.web_run("no head here")
        self.a.orch_events = mine
        with unittest.mock.patch.dict(os.environ, {steps.ENV_DRY_NO_CCN: "1"}):
            self.assertIsNone(steps.next_step(self.a, TASK, mine, "a9"))

    def test_matching_head_continues(self):
        mine = self.web_run(f"HEAD: {self.head}")
        self.a.orch_events = mine
        r = steps.next_step(self.a, TASK, mine, "a9")
        self.assertIsNone(r)

    def test_final_webtest_gate_blocks_stale_pass(self):
        f = self.report(f"HEAD: {OLD_SHA}")
        step = {"id": "final-webtest", "agent": WEB_TESTER, "ok": {PASS}}
        evs = [
            {"ev": EV_DISPATCH, "id": "a1", "task": "final-webtest", "agent": WEB_TESTER, "t": 1},
            {"ev": EV_REPORT, "id": "a1", "task": "final-webtest", "role": WEB_TESTER, "verdict": PASS, "file": f, "t": 2},
        ]
        r = steps.run_phase(self.a, evs, [step], 1)
        assert r is not None  # the test asserts a step came back; narrows the Optional for the lines below
        self.assertEqual(r["action"], "ask_main")
        self.assertTrue(r["message"].startswith(f"stale webtest: report HEAD {OLD_SHA}"), r["message"])

    def test_final_webtest_gate_passes_matching_head(self):
        f = self.report(f"HEAD: {self.head}")
        step = {"id": "final-webtest", "agent": WEB_TESTER, "ok": {PASS}}
        evs = [
            {"ev": EV_DISPATCH, "id": "a1", "task": "final-webtest", "agent": WEB_TESTER, "t": 1},
            {"ev": EV_REPORT, "id": "a1", "task": "final-webtest", "role": WEB_TESTER, "verdict": PASS, "file": f, "t": 2},
        ]
        self.assertIsNone(steps.run_phase(self.a, evs, [step], 1))


if __name__ == "__main__":
    unittest.main()
