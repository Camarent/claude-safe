"""worktree manifest and .branch: a truncated file is an error, an empty .branch falls back to the default name."""

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import gitx, worktree
from pipeline.configuration import JOB_BRANCH_PREFIX


class ManifestTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.path = worktree._manifest(self.d, "s")
        os.makedirs(os.path.dirname(self.path))

    def test_truncated_manifest_is_an_error_not_empty(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write('{"t1": {"branch"')  # torn write
        with self.assertRaises(RuntimeError):
            worktree.read_manifest(self.d, "s")

    def test_missing_manifest_is_empty(self):
        self.assertEqual(worktree.read_manifest(self.d, "s"), {})

    def test_write_is_atomic(self):
        with mock.patch("pipeline.util.os.replace", wraps=os.replace) as rep:
            worktree._write_manifest(self.d, "s", {"t1": {"branch": "b"}})
        self.assertTrue(rep.called)
        self.assertEqual(worktree.read_manifest(self.d, "s"), {"t1": {"branch": "b"}})


class BranchFileTest(unittest.TestCase):
    def test_empty_branch_file_falls_back_to_default(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        root = os.path.join(d, ".wt", "s", "t1", "repo")
        os.makedirs(os.path.dirname(root))
        with open(os.path.join(os.path.dirname(root), ".branch"), "w", encoding="utf-8"):
            pass  # empty: a torn write
        with mock.patch.object(worktree, "project", return_value={}), mock.patch.object(worktree, "repo_dir", return_value=os.path.join(d, "repo")):
            _cfg, _repo, _root, branch = worktree.layout(d, "s", "t1")
        self.assertEqual(branch, f"{JOB_BRANCH_PREFIX}s-t1")


GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false"]


class CrashAndVersionTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, True)
        self.repo = os.path.join(self.d, "repo")
        os.makedirs(self.repo)
        for args in (("init", "-q"), ("commit", "-q", "--allow-empty", "-m", "init")):
            subprocess.run([*GIT, "-C", self.repo, *args], check=True, capture_output=True)

    def _patches(self):
        cfg: dict = {}
        return [
            mock.patch.object(worktree, "project", return_value=cfg),
            mock.patch.object(worktree, "repo_dir", return_value=self.repo),
            mock.patch.object(worktree, "work_branch", return_value=gitx.out(self.repo, "rev-parse", "--abbrev-ref", "HEAD")),
        ]

    def test_crash_after_add_leaves_a_live_job(self):
        ps = self._patches()
        for p in ps:
            p.start()
            self.addCleanup(p.stop)
        with mock.patch.object(worktree, "_seed_index", side_effect=OSError("boom")), self.assertRaises(OSError):
            worktree.create(self.d, "s", "t1")
        info = worktree.live_job(self.d, "s", "t1")
        self.assertIsNotNone(info)
        self.assertEqual(set(info), {"branch", "root", "base", "repos"})
        self.assertTrue(os.path.isdir(info["root"]))
        again = worktree.create(self.d, "s", "t1")  # a retry completes the entry
        self.assertEqual(again["root"], info["root"])

    def test_a_failed_add_leaves_no_manifest_entry_and_an_old_entry_stays(self):
        ps = self._patches()
        for p in ps:
            p.start()
            self.addCleanup(p.stop)
        with mock.patch.object(worktree, "_add", side_effect=RuntimeError("git said no")), self.assertRaises(RuntimeError):
            worktree.create(self.d, "s", "t1")
        self.assertNotIn("t1", worktree.read_manifest(self.d, "s"))
        info = worktree.create(self.d, "s", "t1")  # works afterwards
        with mock.patch.object(worktree, "_add", side_effect=RuntimeError("again")), self.assertRaises(RuntimeError):
            worktree.create(self.d, "s", "t1")  # a rework create that fails keeps the entry the earlier create made
        self.assertEqual(worktree.read_manifest(self.d, "s")["t1"]["root"], info["root"])

    def test_old_git_gives_a_clear_error(self):
        out = subprocess.CompletedProcess([], 0, stdout="git version 2.30.1\n", stderr="")
        with mock.patch.object(worktree, "_GIT_VERSION", []), mock.patch.object(worktree.subprocess, "run", return_value=out) as run:
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, r"git >= 2\.38 needed"):
                    worktree._need_merge_tree()
            self.assertEqual(run.call_count, 1)  # read once
        out = subprocess.CompletedProcess([], 0, stdout="git version 2.43.0\n", stderr="")
        with mock.patch.object(worktree, "_GIT_VERSION", []), mock.patch.object(worktree.subprocess, "run", return_value=out):
            worktree._need_merge_tree()

    def test_save_patch_failure_leaves_one_stderr_line(self):
        err = io.StringIO()
        with mock.patch.object(worktree, "_base_branch", side_effect=RuntimeError("bad")), redirect_stderr(err):
            worktree._save_patch(self.d, "s", "t1", "", self.repo, "b", "w")
        self.assertIn("swallowed in worktree._save_patch: RuntimeError: bad", err.getvalue())


if __name__ == "__main__":
    unittest.main()
