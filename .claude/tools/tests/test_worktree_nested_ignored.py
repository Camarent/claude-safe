"""nested_repos skips a git-ignored dir whose .git is a stray empty folder, keeps a real ignored repo."""

import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import worktree
from tests.support.tmpdirs import tracked_mkdtemp


def _git(cwd, *args):
    subprocess.run(["git", "-C", cwd, *args], check=True, capture_output=True)


class NestedIgnoredTest(unittest.TestCase):
    def setUp(self):
        self.repo = os.path.realpath(tracked_mkdtemp())
        _git(self.repo, "init", "-q")
        with open(os.path.join(self.repo, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("stray/\nreal/\n")

    def test_stray_empty_git_ignored_is_skipped(self):
        os.makedirs(os.path.join(self.repo, "stray", ".git"))
        self.assertEqual(worktree.nested_repos(self.repo), [])

    def test_stray_not_ignored_is_kept(self):
        os.makedirs(os.path.join(self.repo, "other", ".git"))
        self.assertEqual(worktree.nested_repos(self.repo), ["other"])

    def test_real_ignored_repo_is_kept(self):
        real = os.path.join(self.repo, "real")
        os.makedirs(real)
        _git(real, "init", "-q")
        self.assertEqual(worktree.nested_repos(self.repo), ["real"])

    def test_not_a_git_repo_no_raise(self):
        d = tracked_mkdtemp()
        os.makedirs(os.path.join(d, "x", ".git"))
        # the temp dir may sit inside an ignoring repo (TMPDIR under a checkout): stop git at its parent
        with mock.patch.dict(os.environ, {"GIT_CEILING_DIRECTORIES": os.path.dirname(d)}):
            self.assertEqual(worktree.nested_repos(d), ["x"])
