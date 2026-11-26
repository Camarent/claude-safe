"""plan_check.check_git_paths: safe.directory and a timeout on git, and a missing git is a problem line, not a crash.
plan_rules.check_untracked: a Predicted path git ignores is a problem line naming the path; a tracked path is not."""

import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import plan_check, plan_rules
from tests.support.tmpdirs import tracked_mkdtemp


def _git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True)


class GitPathsTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.repo = os.path.join(self.d, "repo")
        os.makedirs(os.path.join(self.repo, "web"))
        with open(os.path.join(self.d, "plan-item.md"), "w", encoding="utf-8") as f:
            f.write("Predicted paths: web/new.js\n")

    def test_git_missing_is_a_problem_line(self):
        with mock.patch.object(plan_check.subprocess, "run", side_effect=FileNotFoundError("git")):
            got = plan_check.check_git_paths(self.d, "plan-item", self.repo)
        self.assertEqual(len(got), 1)
        self.assertIn("web/new.js", got[0])

    def test_git_runs_with_safe_directory_and_timeout(self):
        ok = subprocess.CompletedProcess([], 0, "true\n", "")
        with mock.patch.object(plan_check.subprocess, "run", return_value=ok) as run:
            self.assertEqual(plan_check.check_git_paths(self.d, "plan-item", self.repo), [])
        args, kw = run.call_args
        self.assertIn("safe.directory=*", " ".join(args[0]) + " " + str(kw.get("env", {})))
        self.assertIsNotNone(kw.get("timeout"))


class IgnoredPredictedPathTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.repo = os.path.join(self.d, "repo")
        os.makedirs(os.path.join(self.repo, "mat"))
        _git(self.repo, "init", "-q")
        with open(os.path.join(self.repo, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("mat/\n")
        for n in ("mat/x.txt", "tracked.py"):
            with open(os.path.join(self.repo, n), "w", encoding="utf-8") as f:
                f.write("x\n")
        _git(self.repo, "add", "tracked.py")

    def _check(self, predicted):
        with open(os.path.join(self.d, "plan-item.md"), "w", encoding="utf-8") as f:
            f.write(f"Predicted paths: {predicted}\n")
        return plan_rules.check_untracked(self.d, "plan-item", self.repo)

    def test_ignored_predicted_path_is_a_problem_line_naming_it(self):
        got = self._check("mat/new.txt")  # not created yet: still named
        self.assertEqual(len(got), 1)
        self.assertIn("mat/new.txt is ignored by git", got[0])

    def test_ignored_existing_path_is_named_as_ignored(self):
        got = self._check("mat/x.txt")
        self.assertEqual(len(got), 1)
        self.assertIn("mat/x.txt is ignored by git", got[0])

    def test_tracked_path_is_not_reported_as_ignored(self):
        self.assertEqual(self._check("tracked.py"), [])

    def test_nested_repo_ignored_path_is_named_and_tracked_is_not(self):
        sub = os.path.join(self.repo, "sub")
        os.makedirs(os.path.join(sub, "out"))
        _git(sub, "init", "-q")
        with open(os.path.join(sub, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("out/\n")
        with open(os.path.join(sub, "kept.py"), "w", encoding="utf-8") as f:
            f.write("x\n")
        _git(sub, "add", "kept.py")
        got = self._check("sub/out/new.txt")
        self.assertEqual(len(got), 1)
        self.assertIn("sub/out/new.txt is ignored by git", got[0])
        self.assertEqual(self._check("sub/kept.py"), [])


if __name__ == "__main__":
    unittest.main()
