"""Characterization of pr_ops._clean_repo and _sweep branches (real temp git repos, gh faked)."""

import os
import subprocess
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import pr_ops
from tests.support.gitrepo import git
from tests.support.tmpdirs import tracked_mkdtemp


class CleanRepo(unittest.TestCase):
    def setUp(self):
        self.root = tracked_mkdtemp()
        self.d = os.path.join(self.root, "w")
        os.makedirs(self.d)
        git(self.d, "init", "-q", "-b", "master")
        git(self.d, "commit", "-q", "--allow-empty", "-m", "seed")
        self.a = SimpleNamespace(dir=self.d, stage="s", name="s")
        self.cfg = {}

    def clean(self, main=False):
        return pr_ops._clean_repo(self.a, self.d, self.cfg, "stage/s", main)

    def test_already_clean(self):
        self.assertEqual(self.clean(), "already clean")

    def test_unlanded_branch_blocks(self):
        git(self.d, "switch", "-q", "-c", "stage/s")
        with open(os.path.join(self.d, "f"), "w") as f:
            f.write("a")
        git(self.d, "add", "f")
        git(self.d, "commit", "-qm", "work")
        with self.assertRaises(pr_ops.Blocked):
            self.clean()

    def test_landed_branch_switches_and_deletes(self):
        git(self.d, "switch", "-q", "-c", "stage/s")
        self.assertEqual(self.clean(), "on master; deleted stage/s")

    def test_creates_release_from_origin(self):
        origin = os.path.join(self.root, "o.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "master", origin], check=True)
        git(self.d, "remote", "add", "origin", origin)
        git(self.d, "push", "-q", "origin", "master")
        git(self.d, "fetch", "-q", "origin")
        git(self.d, "switch", "-q", "-c", "other")
        git(self.d, "branch", "-D", "master")
        self.assertEqual(self.clean(), "created master from origin/master")


class Sweep(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        git(self.d, "init", "-q", "-b", "master")
        git(self.d, "commit", "-q", "--allow-empty", "-m", "seed")

    def sweep(self, table):
        real = pr_ops._run

        def run(args, cwd=None, timeout=120):
            if args[0] == "gh":
                return table(args)
            return real(args, cwd, timeout)

        with mock.patch.object(pr_ops, "_run", side_effect=run):
            return pr_ops._sweep(self.d, {})

    def test_list_failure_blocks(self):
        with self.assertRaisesRegex(pr_ops.Blocked, "gh pr list failed: nope"):
            self.sweep(lambda a: SimpleNamespace(returncode=1, stdout="", stderr="nope"))

    def test_merge_failure_and_fetch_failure_reported(self):
        prs = '[{"number": 1, "title": "t", "statusCheckRollup": [{"status": "COMPLETED", "conclusion": "SUCCESS"}]}]'

        def table(a):
            if a[2] == "list":
                return SimpleNamespace(returncode=0, stdout=prs, stderr="")
            return SimpleNamespace(returncode=1, stdout="", stderr="conflict")

        text = self.sweep(table)
        self.assertIn("#1 skipped (merge failed: conflict): t", text)
        self.assertIn("1 merge failed", text)
        self.assertIn("; fetch failed:", text)

    def test_bad_json_means_no_prs(self):
        text = self.sweep(lambda a: SimpleNamespace(returncode=0, stdout="not json", stderr=""))
        self.assertIn("summary: 0 Dependabot PR(s)", text)


if __name__ == "__main__":
    unittest.main()
