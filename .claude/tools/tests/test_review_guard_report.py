import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import review_guard
from pipeline.configuration import ENV_SCRATCH_DIR, WORK_DIR

GUARD = os.path.abspath(review_guard.__file__)


class ReportMainPathTest(unittest.TestCase):
    """A linked worktree may name the reviewer's report by its main-checkout path; every other main path stays denied."""

    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.t = os.path.realpath(t.name)
        self.main = f"{self.t}/main"
        self.wt = f"{self.main}/.wt/job"
        os.makedirs(self.main)
        self.git("init", "-q", cwd=self.main)
        self.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init", cwd=self.main)
        self.git("worktree", "add", "-q", "-b", "job", self.wt, cwd=self.main)

    def git(self, *args, cwd):
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    def test_report_file_in_main_checkout_allowed(self):  # C1
        report = f"{self.main}/{WORK_DIR}/pipeline-tuning/reports/report-a4.md"
        self.assertEqual(review_guard.main_checkout_why(report, self.wt), "")

    def test_other_main_path_still_denied(self):  # C1: a source file, and a non-report file under .work/
        for path in (f"{self.main}/src/a.py", f"{self.main}/{WORK_DIR}/pipeline-tuning/notes.md"):
            why = review_guard.main_checkout_why(path, self.wt)
            self.assertIn("Edit the job worktree, not the main checkout", why, path)

    def test_hook_denial_is_logged(self):  # C3: a denied Edit writes one line to the stage scratch log
        scratch = f"{self.t}/scratch"
        os.makedirs(scratch)
        payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": f"{self.main}/src/a.py"}, "cwd": self.wt})
        env = {k: v for k, v in os.environ.items() if k != ENV_SCRATCH_DIR}
        env[ENV_SCRATCH_DIR] = scratch
        out = subprocess.run([sys.executable, "-I", GUARD], check=False, input=payload, capture_output=True, text=True, timeout=30, env=env, cwd=os.sep)
        self.assertIn("permissionDecision", out.stdout)
        with open(os.path.join(scratch, "denials.jsonl"), encoding="utf-8") as f:
            lines = [json.loads(x) for x in f.read().splitlines()]
        self.assertEqual([x["rule"] for x in lines], ["main_checkout"])
        self.assertEqual(lines[0]["command"], f"{self.main}/src/a.py")
        self.assertEqual(lines[0]["cwd"], self.wt)


if __name__ == "__main__":
    unittest.main()
