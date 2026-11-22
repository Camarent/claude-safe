"""orch retry --verify: the task's Test lines and Done gates run on its existing job commit; green records DONE with a report, red records nothing; no dev is started either way."""

import argparse
import contextlib
import io
import json
import os
import sys
import unittest
import unittest.mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
from pipeline import cli_orch  # noqa: E402
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402


class RetryVerifyTest(unittest.TestCase):
    def setUp(self):
        self.t = tracked_mkdtemp()
        os.makedirs(f"{self.t}/.work/s")
        with open(f"{self.t}/.work/s/verify-step.md", "w") as f:
            f.write("Predicted paths: src/x\nTest: scripts/dev.sh test x.py\n")
        self.checks = unittest.mock.patch.object(cli_orch, "_checks_green", return_value=("Test lines green", 2))
        self.commits = unittest.mock.patch.object(cli_orch, "_job_has_commits", return_value=True)
        self.refresh = unittest.mock.patch.object(cli_orch, "_refresh_job", side_effect=AssertionError("verify must not refresh the job"))
        self.mock_checks = self.checks.start()
        self.addCleanup(self.checks.stop)
        self.commits.start()
        self.addCleanup(self.commits.stop)
        self.refresh.start()
        self.addCleanup(self.refresh.stop)

    def verify(self):
        a = argparse.Namespace(cmd="retry", stage="s", dir=self.t, step="verify-step", note="", from_step="", addendum="", verify=True)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            cli_orch.cmd_reset(a)
        return out.getvalue()

    def log(self):
        path = f"{self.t}/.work/s/orch.jsonl"
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def test_green_records_done_report_without_dev(self):
        out = self.verify()
        self.assertIn('"recorded": "done"', out)
        self.mock_checks.assert_called_once()
        rep = [e for e in self.log() if e["ev"] == "report"]
        self.assertEqual(len(rep), 1)
        self.assertEqual(rep[0]["verdict"], "DONE")
        self.assertEqual(rep[0]["id"], "retry-1")
        self.assertEqual(rep[0]["task"], "verify-step")
        with open(rep[0]["file"], encoding="utf-8") as f:
            self.assertTrue(f.readline().startswith("VERDICT: DONE"))
        self.assertFalse([e for e in self.log() if e["ev"] in ("dispatch", "retry")])

    def test_mutation_red_test_starts_no_dev(self):
        self.mock_checks.return_value = None
        out = self.verify()
        self.assertIn('"verified": false', out)
        self.assertEqual(self.log(), [])
        self.assertFalse(os.path.exists(f"{self.t}/.work/s/report-retry-1.md"))

    def test_no_job_commit_checks_nothing(self):
        with unittest.mock.patch.object(cli_orch, "_job_has_commits", return_value=False):
            out = self.verify()
        self.mock_checks.assert_not_called()
        self.assertIn('"verified": false', out)
        self.assertEqual(self.log(), [])

    def test_report_ids_continue(self):
        self.verify()
        self.verify()
        ids = [e["id"] for e in self.log() if e["ev"] == "report"]
        self.assertEqual(ids, ["retry-1", "retry-2"])
