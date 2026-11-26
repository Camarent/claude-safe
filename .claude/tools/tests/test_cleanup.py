import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
import unittest.mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import wt  # noqa: E402  (import after the sys.path insert)
from pipeline import cleanup, worktree  # noqa: E402  (import after the sys.path insert)
from pipeline.configuration import ACCEPTANCE_TMP_PREFIX, CLEANUP_DRY_RUN_FLAG, CLEANUP_TMP_MIN_AGE_S  # noqa: E402  (import after the sys.path insert)
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402  (import after the sys.path insert)

# Task names as the assertions compare them (the job folder and branch are built from the same numbers).
T1, T2, T3 = "task-1", "task-2", "task-3"
LATER = 10**10  # a clock far past every file's mtime: all temp dirs count as old


def sh(*cmd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *cmd], check=True, capture_output=True)


def commit_in(path, name):
    open(f"{path}/{name}", "w").write("x")
    sh("-C", path, "add", name)
    sh("-C", path, "commit", "-qm", name)


class CleanupTest(unittest.TestCase):
    def setUp(self):
        ident = {f"GIT_{w}_{k}": v for w in ("AUTHOR", "COMMITTER") for k, v in (("NAME", "t"), ("EMAIL", "t@t"))}
        p = unittest.mock.patch.dict(os.environ, ident)
        p.start()
        self.addCleanup(p.stop)
        self.t = tracked_mkdtemp()
        self.m = f"{self.t}/main"
        os.makedirs(self.m)
        os.makedirs(f"{self.t}/.work/s")
        json.dump({"repo": "main", "branch_work": "dev"}, open(f"{self.t}/pipeline.json", "w"))
        sh("-C", self.m, "init", "-q", "-b", "dev")
        commit_in(self.m, "f")
        for n in (1, 2, 3):
            open(f"{self.t}/.work/s/task-{n}.md", "w").write("Predicted paths: f")

    def _job(self, n):
        info = wt.create(self.t, "s", f"task-{n}")
        return info["root"]

    def _gone_job(self, n, merged=True):
        """A job worktree detached at its commit (clean), then its branch deleted: the branch is gone, the folder stays."""
        root = self._job(n)
        if merged:
            commit_in(self.m, f"w{n}")  # work branch moves on; the job folder has nothing of its own
        sh("-C", root, "checkout", "-q", "--detach")
        sh("-C", self.m, "branch", "-D", f"job/s-task-{n}")
        return root

    def _kinds(self, res):
        return sorted((task, why) for _stage, task, why in res["worktrees"])

    def test_merged_and_gone_removed_live_kept(self):
        merged = self._job(1)
        commit_in(merged, "a")
        sh("-C", self.m, "merge", "-q", "job/s-task-1")  # the work branch now holds the job's tree
        live = self._job(2)
        commit_in(live, "b")  # committed, not merged: live
        gone = self._gone_job(3)

        res = cleanup.cleanup(self.t, tmp_root=self.t)

        self.assertEqual(self._kinds(res), [(T1, "merged"), (T3, "gone")])
        self.assertEqual(res["kept"], [("s", T2)])
        self.assertFalse(os.path.exists(merged))
        self.assertFalse(os.path.exists(gone))
        self.assertTrue(os.path.isdir(live))
        self.assertIn(T2, worktree.list_wt(self.t, "s"))

    def test_gone_branch_with_uncommitted_work_is_kept(self):
        root = self._gone_job(3)
        open(f"{root}/scratch", "w").write("unsaved")

        res = cleanup.cleanup(self.t, tmp_root=self.t)

        self.assertEqual(res["worktrees"], [])
        self.assertTrue(os.path.isfile(f"{root}/scratch"))

    def test_acceptance_temp_dirs_removed_others_kept(self):
        tmp = tracked_mkdtemp()
        os.makedirs(f"{tmp}/{ACCEPTANCE_TMP_PREFIX}1/sub")
        os.makedirs(f"{tmp}/other-dir")
        open(f"{tmp}/{ACCEPTANCE_TMP_PREFIX}file", "w").write("x")  # a file with the prefix is not a run dir

        res = cleanup.cleanup(self.t, tmp_root=tmp, now=LATER)

        self.assertEqual(res["tmp"], [f"{tmp}/{ACCEPTANCE_TMP_PREFIX}1"])
        self.assertFalse(os.path.exists(f"{tmp}/{ACCEPTANCE_TMP_PREFIX}1"))
        self.assertTrue(os.path.isdir(f"{tmp}/other-dir"))
        self.assertTrue(os.path.isfile(f"{tmp}/{ACCEPTANCE_TMP_PREFIX}file"))

    def test_gone_branch_with_unmerged_commits_is_kept(self):
        root = self._gone_job(3, merged=False)
        commit_in(root, "c3")

        res = cleanup.cleanup(self.t, tmp_root=self.t)

        self.assertEqual(res["worktrees"], [])
        self.assertTrue(os.path.isdir(root))

    def test_recent_acceptance_dir_is_kept(self):
        tmp = tracked_mkdtemp()
        os.makedirs(f"{tmp}/{ACCEPTANCE_TMP_PREFIX}1")
        now = os.path.getmtime(f"{tmp}/{ACCEPTANCE_TMP_PREFIX}1") + CLEANUP_TMP_MIN_AGE_S - 1

        res = cleanup.cleanup(self.t, tmp_root=tmp, now=now)

        self.assertEqual(res["tmp"], [])
        self.assertTrue(os.path.isdir(f"{tmp}/{ACCEPTANCE_TMP_PREFIX}1"))

    def test_dry_run_lists_and_changes_nothing(self):
        merged = self._job(1)
        commit_in(merged, "a")
        sh("-C", self.m, "merge", "-q", "job/s-task-1")
        tmp = tracked_mkdtemp()
        os.makedirs(f"{tmp}/{ACCEPTANCE_TMP_PREFIX}1")

        res = cleanup.cleanup(self.t, tmp_root=tmp, dry_run=True, now=LATER)

        self.assertTrue(res["dry_run"])
        self.assertEqual(self._kinds(res), [(T1, "merged")])
        self.assertEqual(res["tmp"], [f"{tmp}/{ACCEPTANCE_TMP_PREFIX}1"])
        self.assertTrue(os.path.isdir(merged))
        self.assertTrue(os.path.isdir(f"{tmp}/{ACCEPTANCE_TMP_PREFIX}1"))

    def test_cli_dry_run_flag(self):
        merged = self._job(1)
        commit_in(merged, "a")
        sh("-C", self.m, "merge", "-q", "job/s-task-1")
        tmp = tracked_mkdtemp()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cleanup.main(["--dir", self.t, "--tmp-root", tmp, CLEANUP_DRY_RUN_FLAG])
        self.assertEqual(rc, 0)
        self.assertIn(f"would remove worktree s/{T1} (merged)", out.getvalue())
        self.assertTrue(os.path.isdir(merged))


if __name__ == "__main__":
    unittest.main()
