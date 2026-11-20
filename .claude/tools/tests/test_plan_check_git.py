"""plan_check.check_git_paths: safe.directory and a timeout on git, and a missing git is a problem line, not a crash."""

import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check
from tests.support.tmpdirs import tracked_mkdtemp


class GitPathsTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.repo = os.path.join(self.d, "repo")
        os.makedirs(os.path.join(self.repo, "web"))
        with open(os.path.join(self.d, "task-1.md"), "w", encoding="utf-8") as f:
            f.write("Predicted paths: web/new.js\n")

    def test_git_missing_is_a_problem_line(self):
        with mock.patch.object(plan_check.subprocess, "run", side_effect=FileNotFoundError("git")):
            got = plan_check.check_git_paths(self.d, "task-1", self.repo)
        self.assertEqual(len(got), 1)
        self.assertIn("web/new.js", got[0])

    def test_git_runs_with_safe_directory_and_timeout(self):
        ok = subprocess.CompletedProcess([], 0, "true\n", "")
        with mock.patch.object(plan_check.subprocess, "run", return_value=ok) as run:
            self.assertEqual(plan_check.check_git_paths(self.d, "task-1", self.repo), [])
        args, kw = run.call_args
        self.assertIn("safe.directory=*", " ".join(args[0]) + " " + str(kw.get("env", {})))
        self.assertIsNotNone(kw.get("timeout"))


if __name__ == "__main__":
    unittest.main()
