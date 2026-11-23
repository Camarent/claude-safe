import json
import os
import subprocess
import sys
import unittest
import unittest.mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import wt  # noqa: E402  (import after the sys.path insert)
from pipeline import cleanup  # noqa: E402  (import after the sys.path insert)
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402  (import after the sys.path insert)

STAGE = "s"
T1, T2, T3 = "task-1", "task-2", "task-3"


def job_branch(task):
    return f"job/{STAGE}-{task}"


def sh(*cmd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *cmd], check=True, capture_output=True)


def commit_in(path, name):
    open(f"{path}/{name}", "w").write("x")
    sh("-C", path, "add", name)
    sh("-C", path, "commit", "-qm", name)


def local_branches(repo):
    out = subprocess.run(["git", "-C", repo, "for-each-ref", "--format=%(refname:short)", "refs/heads/job/"], capture_output=True, text=True, check=True)
    return out.stdout.split()


class CleanupBranchesTest(unittest.TestCase):
    """Local job/* branches: merged and free ones go, unmerged and worktree-backed ones stay. The project repo is a temp repo with
    branch_work `dev`; GIT_*_* identity is set through patch.dict so the test never reads the user's git config."""

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
        return wt.create(self.t, "s", f"task-{n}")["root"]

    def _branch_without_worktree(self, name, commits):
        """A job branch with no worktree: `commits` new commits on it (0 = at dev's head, merged; 1+ = not merged)."""
        sh("-C", self.m, "branch", name)
        if commits:
            sh("-C", self.m, "checkout", "-q", name)
            commit_in(self.m, "job-work")
            sh("-C", self.m, "checkout", "-q", "dev")

    def test_merged_branch_without_worktree_listed_then_deleted(self):
        self._branch_without_worktree(job_branch(T1), commits=0)

        dry = cleanup.cleanup(self.t, tmp_root=self.t, dry_run=True)
        self.assertEqual(dry["branches"], [(job_branch(T1), "dev")])
        self.assertEqual(local_branches(self.m), [job_branch(T1)])  # dry run deletes nothing

        res = cleanup.cleanup(self.t, tmp_root=self.t)
        self.assertEqual(res["branches"], [(job_branch(T1), "dev")])
        self.assertEqual(local_branches(self.m), [])

    def test_unmerged_branch_without_worktree_is_kept(self):
        self._branch_without_worktree(job_branch(T2), commits=1)

        res = cleanup.cleanup(self.t, tmp_root=self.t)

        self.assertEqual(res["branches"], [])
        self.assertEqual(local_branches(self.m), [job_branch(T2)])

    def test_branch_of_live_worktree_is_kept_even_when_merged(self):
        root = self._job(3)  # job/s-task-3 at dev's head: an ancestor of dev, so merged
        open(f"{root}/scratch", "w").write("unsaved")  # uncommitted work keeps the worktree live

        res = cleanup.cleanup(self.t, tmp_root=self.t)

        self.assertEqual(res["worktrees"], [])
        self.assertEqual(res["branches"], [])
        self.assertIn(job_branch(T3), local_branches(self.m))

    def test_merged_worktree_branch_goes_with_its_worktree(self):
        root = self._job(1)
        commit_in(root, "a")
        sh("-C", self.m, "merge", "-q", job_branch(T1))  # the work branch holds the job's tree

        dry = cleanup.cleanup(self.t, tmp_root=self.t, dry_run=True)
        self.assertEqual(dry["worktrees"], [(STAGE, T1, "merged")])
        self.assertEqual(dry["branches"], [])  # the worktree removal takes its branch: no separate line
        self.assertTrue(os.path.isdir(root))

        res = cleanup.cleanup(self.t, tmp_root=self.t)
        self.assertEqual(res["worktrees"], [(STAGE, T1, "merged")])
        self.assertEqual(res["branches"], [])
        self.assertFalse(os.path.exists(root))
        self.assertEqual(local_branches(self.m), [])

    def test_prune_branches_deletes_merged_orphans_and_names_unmerged(self):
        self._branch_without_worktree(job_branch(T1), commits=0)  # merged orphan
        self._branch_without_worktree(job_branch(T2), commits=1)  # unmerged orphan

        pruned, unmerged = cleanup.prune_branches(self.t)

        self.assertEqual(pruned, [(job_branch(T1), "dev")])
        self.assertEqual(unmerged, [job_branch(T2)])
        self.assertEqual(local_branches(self.m), [job_branch(T2)])

    def test_prune_branches_keeps_branch_of_folder_without_worktree(self):
        self._branch_without_worktree(job_branch(T3), commits=0)  # merged, but its .wt folder is live
        os.makedirs(f"{self.t}/.wt/{STAGE}/{T3}")
        open(f"{self.t}/.wt/{STAGE}/{T3}/.branch", "w").write(job_branch(T3) + "\n")

        pruned, unmerged = cleanup.prune_branches(self.t)

        self.assertEqual((pruned, unmerged), ([], []))
        self.assertEqual(local_branches(self.m), [job_branch(T3)])

    def test_prune_branches_keeps_branch_checked_out_in_a_worktree(self):
        self._branch_without_worktree(job_branch(T1), commits=0)  # merged
        sh("-C", self.m, "worktree", "add", "-q", f"{self.t}/elsewhere", job_branch(T1))
        self.addCleanup(sh, "-C", self.m, "worktree", "remove", "--force", f"{self.t}/elsewhere")

        pruned, unmerged = cleanup.prune_branches(self.t)

        self.assertEqual((pruned, unmerged), ([], []))
        self.assertEqual(local_branches(self.m), [job_branch(T1)])

    def test_unmerged_worktree_branch_is_kept(self):
        root = self._job(2)
        commit_in(root, "b")  # committed, not merged: live

        res = cleanup.cleanup(self.t, tmp_root=self.t)

        self.assertEqual(res["kept"], [(STAGE, T2)])
        self.assertEqual(res["branches"], [])
        self.assertIn(job_branch(T2), local_branches(self.m))


if __name__ == "__main__":
    unittest.main()
