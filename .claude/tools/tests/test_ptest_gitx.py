"""ptest's main checkout git reads go through pipeline.gitx: real git on a scratch repo, no git at all, and the gitx call is the one that is used."""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ptest
from pipeline import gitx


def git(repo, *args):
    subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True, capture_output=True)


class MainCheckoutGitTest(unittest.TestCase):
    def setUp(self):
        repo = tempfile.TemporaryDirectory()  # a scratch main checkout, never the real one
        self.addCleanup(repo.cleanup)
        self.repo = repo.name
        git(self.repo, "init", "-q", "-b", "master")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "init")
        patcher = mock.patch.object(ptest, "HERE", self.repo)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_claude_status_lists_uncommitted_claude_files(self):
        os.makedirs(os.path.join(self.repo, ".claude"))
        with open(os.path.join(self.repo, ".claude", "x.py"), "w", encoding="utf-8") as f:
            f.write("x = 1\n")
        self.assertEqual(ptest.main_claude_status(), {"?? .claude/"})

    def test_branch_state_reads_through_gitx(self):
        git(self.repo, "branch", "stage/a")
        git(self.repo, "branch", "job/retro-m25/x")
        with mock.patch.object(gitx, "try_text", wraps=gitx.try_text) as spy:
            self.assertEqual(ptest.main_branch_state(), ("master", ["master", "stage/a"]))
        self.assertTrue(spy.called)

    def test_claude_status_uses_gitx_try_text(self):
        with mock.patch.object(gitx, "try_text", return_value=" M .claude/a.md\n") as spy:
            self.assertEqual(ptest.main_claude_status(), {" M .claude/a.md"})
        self.assertEqual(spy.call_args.args[1:], ("status", "--porcelain", "--", ".claude"))

    def test_no_git_gives_empty_state(self):
        with mock.patch.object(gitx.subprocess, "run", side_effect=FileNotFoundError("git")):
            self.assertEqual(ptest.main_claude_status(), set())
            self.assertEqual(ptest.main_branch_state(), ("", []))


class SkipFailsRunTest(unittest.TestCase):
    def test_skips_fail_only_with_ci_flag(self):
        with mock.patch.dict(os.environ, {ptest.ENV_FAIL_ON_SKIP: "1"}):
            self.assertTrue(ptest.skips_fail_run(2))
            self.assertFalse(ptest.skips_fail_run(0))
        with mock.patch.dict(os.environ, {ptest.ENV_FAIL_ON_SKIP: "0"}):
            self.assertFalse(ptest.skips_fail_run(2))


if __name__ == "__main__":
    unittest.main()
