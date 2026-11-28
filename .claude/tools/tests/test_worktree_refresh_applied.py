"""worktree.refresh: job commits already on the stage branch under another sha are not replayed; commits not on it still rebase."""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import gitx, worktree, worktree_merge

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"]


def _git(cwd, *args):
    subprocess.run([*GIT, "-C", cwd, *args], check=True, capture_output=True)


def _write(path, name, text):
    with open(os.path.join(path, name), "w", encoding="utf-8") as f:
        f.write(text)


class AppliedRefreshTest(unittest.TestCase):
    def setUp(self):
        ident = {f"GIT_{w}_{k}": v for w in ("AUTHOR", "COMMITTER") for k, v in (("NAME", "test"), ("EMAIL", "test@example.invalid"))}
        p = mock.patch.dict(os.environ, ident)
        p.start()
        self.addCleanup(p.stop)
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.repo = os.path.join(self.d, "repo")
        os.makedirs(self.repo)
        _git(self.repo, "init", "-q")
        _write(self.repo, "a.txt", "a\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "init")
        self.base = gitx.out(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        self.wt = os.path.join(self.d, "job", "repo")
        _git(self.repo, "worktree", "add", "-q", "-b", "job", self.wt)
        info = {"branch": "job", "root": self.wt, "base": self.base, "repos": {"repo": self.wt}}
        patches = [
            mock.patch.object(worktree, "live_job", return_value=info),
            mock.patch.object(worktree, "layout", return_value=({}, self.repo, self.wt, "job")),
            mock.patch.object(worktree_merge, "layout", return_value=({}, self.repo, self.wt, "job")),
            mock.patch.object(worktree, "work_branch", return_value=self.base),
            mock.patch.object(worktree_merge, "work_branch", return_value=self.base),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _job_commit(self, name, text):
        _write(self.wt, name, text)
        _git(self.wt, "add", "-A")
        _git(self.wt, "commit", "-q", "-m", f"job {name}")
        return gitx.out(self.wt, "rev-parse", "HEAD").strip()

    def test_commit_applied_on_stage_under_new_sha_is_not_replayed(self):
        # the job adds d.txt; the stage took the same change as a cherry-pick (new sha), then edited it (add/add: job tree no longer merges)
        sha = self._job_commit("d.txt", "d\n")
        # a later committer date: the same second and parent would give the same sha back, not a new one
        with mock.patch.dict(os.environ, {"GIT_COMMITTER_DATE": "2001-01-01T00:00:00 +0000"}):
            _git(self.repo, "cherry-pick", sha)
        self.assertNotEqual(gitx.out(self.repo, "rev-parse", "HEAD").strip(), sha)
        _write(self.repo, "d.txt", "d2\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "stage edits d")
        head = gitx.out(self.wt, "rev-parse", "HEAD").strip()
        res = worktree.refresh(self.d, "s", "t")  # no RefreshConflict
        self.assertEqual(res["rebased"], [])
        self.assertEqual(gitx.out(self.wt, "rev-parse", "HEAD").strip(), head)

    def test_commit_not_on_stage_is_still_rebased(self):
        # control: the job commit is not on the stage, so the job rebases onto the moved stage
        self._job_commit("g.txt", "g\n")
        _write(self.repo, "c.txt", "c\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-q", "-m", "main moves")
        head = gitx.out(self.wt, "rev-parse", "HEAD").strip()
        res = worktree.refresh(self.d, "s", "t")
        self.assertEqual(res["rebased"], ["repo"])
        self.assertNotEqual(gitx.out(self.wt, "rev-parse", "HEAD").strip(), head)


if __name__ == "__main__":
    unittest.main()
