"""worktree.refresh: a timed-out rebase is aborted, discard keeps the LINK symlinks."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import gitx, worktree

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"]


def _git(cwd, *args):
    subprocess.run([*GIT, "-C", cwd, *args], check=True, capture_output=True)


class RefreshTest(unittest.TestCase):
    def setUp(self):
        # the rebase commits as the committer: give it an identity so the test does not depend on the host's global git config
        ident = {f"GIT_{w}_{k}": v for w in ("AUTHOR", "COMMITTER") for k, v in (("NAME", "test"), ("EMAIL", "test@example.invalid"))}
        p = mock.patch.dict(os.environ, ident)
        p.start()
        self.addCleanup(p.stop)
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.repo = os.path.join(self.d, "repo")
        os.makedirs(self.repo)
        _git(self.repo, "init", "-q")
        with open(os.path.join(self.repo, ".gitignore"), "w", encoding="utf-8") as f:
            f.write("node_modules/\n")
        with open(os.path.join(self.repo, "a.txt"), "w", encoding="utf-8") as f:
            f.write("a\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")
        self.base = gitx.out(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        self.wt = os.path.join(self.d, "job", "repo")
        _git(self.repo, "worktree", "add", "-q", "-b", "job", self.wt)
        # the main checkout moves on after the job was cut: the job branch must rebase
        with open(os.path.join(self.repo, "c.txt"), "w", encoding="utf-8") as f:
            f.write("c\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "main moves")
        info = {"branch": "job", "root": self.wt, "base": self.base, "repos": {"repo": self.wt}}
        patches = [
            mock.patch.object(worktree, "live_job", return_value=info),
            mock.patch.object(worktree, "layout", return_value=({}, self.repo, self.wt, "job")),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_timed_out_rebase_is_aborted_and_reported(self):
        real_run = gitx.run
        calls = []

        def fake_run(repo, *args, **kw):
            calls.append(args)
            if args[:1] == ("rebase",) and args[1:2] != ("--abort",):
                raise subprocess.TimeoutExpired(cmd="git rebase", timeout=1)
            return real_run(repo, *args, **kw)

        with mock.patch.object(gitx, "run", side_effect=fake_run), self.assertRaises(RuntimeError):
            worktree.refresh(self.d, "s", "t")
        self.assertIn(("rebase", "--abort"), calls)
        self.assertFalse(os.path.isdir(os.path.join(self.wt, ".git", "rebase-merge")))

    def test_discard_keeps_node_modules_symlink(self):
        target = os.path.join(self.d, "deps")
        os.makedirs(target)
        link = os.path.join(self.wt, "node_modules")
        os.symlink(target, link)
        with open(os.path.join(self.wt, "dirty.txt"), "w", encoding="utf-8") as f:
            f.write("dev work\n")
        worktree.refresh(self.d, "s", "t", discard=True)
        self.assertTrue(os.path.islink(link))
        self.assertFalse(os.path.exists(os.path.join(self.wt, "dirty.txt")))

    def test_retry_discards_dirty_leftovers_and_keeps_commits(self):  # the dev is dead (last report DONE): leftovers go, its commit stays
        with open(os.path.join(self.wt, "e.txt"), "w", encoding="utf-8") as f:
            f.write("committed\n")
        _git(self.wt, "add", "-A")
        _git(self.wt, "commit", "-q", "-m", "job work")
        with open(os.path.join(self.wt, "dirty.txt"), "w", encoding="utf-8") as f:
            f.write("leftover\n")
        res = worktree.refresh(self.d, "s", "t", discard=True)
        self.assertEqual(gitx.out(self.wt, "status", "--porcelain").strip(), "")
        self.assertFalse(os.path.exists(os.path.join(self.wt, "dirty.txt")))
        self.assertEqual(res["discarded"], ["repo: ?? dirty.txt"])
        # the rebase onto the moved base rewrites the hash; the commit subject and its file must survive
        self.assertEqual(gitx.out(self.wt, "log", "-1", "--format=%s").strip(), "job work")
        self.assertTrue(os.path.isfile(os.path.join(self.wt, "e.txt")))

    def test_squash_merged_job_is_not_replayed(self):  # the job's change is already on the stage branch (squash): no rebase, marked merged
        with open(os.path.join(self.wt, "d.txt"), "w", encoding="utf-8") as f:
            f.write("d\n")
        _git(self.wt, "add", "-A")
        _git(self.wt, "commit", "-q", "-m", "job work")
        with open(os.path.join(self.repo, "d.txt"), "w", encoding="utf-8") as f:
            f.write("d\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "squash of the job")
        head = gitx.out(self.wt, "rev-parse", "HEAD")
        with mock.patch.object(worktree, "work_branch", return_value=self.base):
            res = worktree.refresh(self.d, "s", "t")
        self.assertEqual(res["rebased"], [])
        self.assertEqual(gitx.out(self.wt, "rev-parse", "HEAD"), head)
        self.assertTrue(os.path.isfile(os.path.join(self.d, "job", ".merged")))


if __name__ == "__main__":
    unittest.main()
