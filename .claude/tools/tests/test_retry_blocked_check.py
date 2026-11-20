"""orch retry of a BLOCKED dev task: green Test lines and gates record done without a model call; red or missing checks leave the normal retry."""

import argparse
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import testrun  # noqa: E402
from pipeline import cli_orch  # noqa: E402


class RetryBlockedCheck(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.t, True)
        os.makedirs(f"{self.t}/.work/s")
        with open(f"{self.t}/.work/s/task-1.md", "w") as f:
            f.write("Test: true\n")
        self.cfg({"gates": [{"name": "g", "cmd": "true"}]})
        ev = [
            {"ev": "dispatch", "task": "task-1", "id": "a1", "agent": "dev", "role": "dev", "model": "haiku"},
            {
                "ev": "report",
                "task": "task-1",
                "id": "a1",
                "role": "dev",
                "verdict": "BLOCKED",
                "file": "f.md",
                "reason": "tests fail in the sandbox environment",
            },
        ]
        with open(f"{self.t}/.work/s/orch.jsonl", "w") as f:
            f.write("".join(json.dumps(e) + "\n" for e in ev))

    def cfg(self, d):
        with open(f"{self.t}/pipeline.json", "w") as f:
            json.dump(d, f)

    def log(self):
        with open(f"{self.t}/.work/s/orch.jsonl", encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def retry(self, verdict="DONE", note="1 Test line(s) green", commits=True):
        a = argparse.Namespace(cmd="retry", stage="s", dir=self.t, step="task-1", note="", from_step="")
        with (
            mock.patch.object(testrun, "run", return_value=(verdict, note)),
            mock.patch.object(testrun, "workdir", return_value=self.t),
            mock.patch.object(cli_orch, "_job_has_commits", return_value=commits),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            cli_orch.cmd_reset(a)

    def test_green_checks_record_done_without_retry_event(self):
        self.retry()
        last = self.log()[-1]
        self.assertEqual((last["ev"], last["task"], last["role"], last["verdict"]), ("report", "task-1", "dev", "DONE"))
        self.assertFalse([e for e in self.log() if e["ev"] == "retry"])

    def test_no_new_commits_keeps_normal_retry(self):
        self.retry(commits=False)
        self.assertEqual(self.log()[-1]["ev"], "retry")

    def test_owner_decision_reason_keeps_normal_retry(self):
        self.reason("needs an owner decision on scope; tests unknown")
        self.retry()
        self.assertEqual(self.log()[-1]["ev"], "retry")

    def test_report_id_follows_max_retry_id(self):
        evs = [*self.log(), {"ev": "report", "task": "x", "id": "retry-7", "role": "dev", "verdict": "DONE", "file": "g.md"}, self.log()[1]]
        with open(f"{self.t}/.work/s/orch.jsonl", "w") as f:
            f.write("".join(json.dumps(e) + "\n" for e in evs))
        self.retry()
        self.assertEqual(self.log()[-1]["id"], "retry-8")

    def reason(self, text):
        evs = self.log()
        evs[1]["reason"] = text
        with open(f"{self.t}/.work/s/orch.jsonl", "w") as f:
            f.write("".join(json.dumps(e) + "\n" for e in evs))

    def test_red_test_line_keeps_normal_retry(self):
        self.retry("FAIL", "boom")
        self.assertEqual(self.log()[-1]["ev"], "retry")

    def test_red_gate_keeps_normal_retry(self):
        self.cfg({"gates": [{"name": "g", "cmd": "false"}]})
        self.retry()
        self.assertEqual(self.log()[-1]["ev"], "retry")

    def test_task_without_test_line_keeps_normal_retry(self):
        self.retry("DONE", "test run skipped: the task has no Test line")
        self.assertEqual(self.log()[-1]["ev"], "retry")


if __name__ == "__main__":
    unittest.main()
