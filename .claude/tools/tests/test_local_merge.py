"""local-merge step: squash the stage branch into the release branch in local-only repos (main repo without origin, nested repos)."""

import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import test_pr_steps as tps
from pipeline import pr_base, pr_mode, pr_ops
from tests.support.gitrepo import git


def seed(path):
    os.makedirs(path, exist_ok=True)
    git(path, "init", "-q", "-b", "master")
    Path(path, "f").write_text("x\n")
    git(path, "add", "f")
    git(path, "commit", "-qm", "init")


class LocalMergeTest(unittest.TestCase):
    def setUp(self):
        ident = {f"GIT_{w}_{k}": v for w in ("AUTHOR", "COMMITTER") for k, v in (("NAME", "t"), ("EMAIL", "t@t"))}
        p = unittest.mock.patch.dict(os.environ, ident)
        p.start()
        self.addCleanup(p.stop)
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        Path(self.d, "pipeline.json").write_text("{}")
        seed(self.d)
        git(self.d, "switch", "-q", "-c", "stage/f")
        for i in (1, 2):
            Path(self.d, f"g{i}").write_text("y\n")
            git(self.d, "add", "-A")
            git(self.d, "commit", "-qm", f"w{i}")
        git(self.d, "switch", "-q", "master")

    def run_step(self):
        return pr_ops.run("local-merge", self.d, "f", "f")

    def count(self, d=None):
        return int(git(d or self.d, "rev-list", "--count", "master"))

    def test_squashes_to_one_commit_default_subject(self):
        v, t = self.run_step()
        self.assertEqual(v, "DONE", t)
        self.assertEqual(self.count(), 2)
        self.assertEqual(git(self.d, "log", "-1", "--format=%s", "master"), "chore(f): squash stage branch stage/f")
        self.assertIn("Co-Authored-By: Claude Sonnet 5.5", git(self.d, "log", "-1", "--format=%B", "master"))
        self.assertTrue(Path(self.d, "g2").exists())
        self.assertTrue(git(self.d, "rev-parse", "--verify", "stage/f"))  # branch kept

    def test_repo_is_left_on_the_release_branch(self):
        git(self.d, "switch", "-q", "stage/f")  # the driver works on the stage branch
        v, t = self.run_step()
        self.assertEqual(v, "DONE", t)
        self.assertEqual(git(self.d, "branch", "--show-current"), "master")
        self.assertEqual(self.count(), 2)

    def test_note_names_the_branch_the_user_was_on(self):
        git(self.d, "switch", "-q", "stage/f")
        v, t = self.run_step()
        self.assertEqual(v, "DONE", t)
        self.assertIn("you were on `stage/f`", t)

    def test_failed_commit_leaves_the_tree_on_the_original_branch(self):
        git(self.d, "switch", "-q", "stage/f")
        bad = SimpleNamespace(returncode=1, stdout="", stderr="hook said no")
        real = pr_base._run
        fake = lambda args, cwd, timeout=120: bad if any("commit.py" in x for x in args) else real(args, cwd, timeout)  # noqa: E731
        with unittest.mock.patch.object(pr_base, "_run", fake), self.assertRaises(pr_ops.Blocked):
            pr_ops._local_merge_repo(self.d, {}, "stage/f", "f", self.d)
        self.assertEqual(git(self.d, "branch", "--show-current"), "stage/f")

    def test_ends_redacts_url_credentials(self):
        self.assertEqual(pr_ops._ends("fatal: https://bob:tok@host/x.git denied"), "fatal: https://***@host/x.git denied")

    def test_released_stage_branch_is_not_recreated(self):
        from pipeline.branches import ensure_work_branch

        self.assertEqual(self.run_step()[0], "DONE")
        v, t = pr_ops.run("cleanup", self.d, "f", "f")
        self.assertEqual(v, "DONE", t)
        self.assertEqual(git(self.d, "branch", "--list", "stage/f"), "")
        os.makedirs(os.path.join(self.d, ".work", "f"))
        Path(self.d, ".work", "f", "orch.jsonl").write_text('{"ev": "report", "task": "branch-cleanup", "verdict": "DONE"}\n')
        self.assertEqual(ensure_work_branch(SimpleNamespace(dir=self.d, stage="f"), "f"), "")
        self.assertEqual(git(self.d, "branch", "--list", "stage/f"), "")
        self.assertEqual(git(self.d, "branch", "--show-current"), "master")

    def test_phase_stage_name_gives_slashless_area(self):
        git(self.d, "branch", pr_mode.branch(SimpleNamespace(dir=self.d), "feat/phases/04-finish"), "stage/f")
        v, t = pr_ops.run("local-merge", self.d, "f", "feat/phases/04-finish")
        self.assertEqual(v, "DONE", t)
        self.assertEqual(git(self.d, "log", "-1", "--format=%s", "master"), "chore(feat): squash stage branch stage/feat")

    def test_subject_file(self):
        os.makedirs(f"{self.d}/.work/f")
        Path(self.d, ".work/f/SUBJECT").write_text("feat(x): do it\nmore\n")
        self.assertEqual(self.run_step()[0], "DONE")
        self.assertEqual(git(self.d, "log", "-1", "--format=%s", "master"), "feat(x): do it")

    def test_idempotent(self):
        self.run_step()
        v, t = self.run_step()
        self.assertEqual(v, "DONE")
        self.assertIn("already merged", t)
        self.assertEqual(self.count(), 2)

    def test_missing_branch_skipped(self):
        v, t = pr_ops.run("local-merge", self.d, "f", "nope")
        self.assertEqual(v, "DONE")
        self.assertIn("skipped", t)
        self.assertEqual(self.count(), 1)

    def test_dirty_tree_blocked(self):
        Path(self.d, "f").write_text("dirty\n")
        v, t = self.run_step()
        self.assertEqual(v, "BLOCKED")
        self.assertIn("dirty", t)
        self.assertEqual(self.count(), 1)

    def test_gitignored_untracked_file_does_not_block(self):
        Path(self.d, ".gitignore").write_text("__pycache__/\n")
        git(self.d, "add", ".gitignore")
        git(self.d, "commit", "-qm", "ignore")
        os.makedirs(f"{self.d}/__pycache__")
        Path(self.d, "__pycache__/x.pyc").write_text("bin\n")
        v, t = self.run_step()
        self.assertEqual(v, "DONE", t)

    def test_non_ignored_untracked_file_blocks(self):
        Path(self.d, "stray.txt").write_text("untracked\n")
        v, t = self.run_step()
        self.assertEqual(v, "BLOCKED")
        self.assertIn("dirty", t)
        self.assertEqual(self.count(), 1)

    def test_conflict_blocked_tree_clean(self):
        git(self.d, "switch", "-q", "stage/f")
        Path(self.d, "f").write_text("stage\n")
        git(self.d, "commit", "-qam", "s")
        git(self.d, "switch", "-q", "master")
        Path(self.d, "f").write_text("master\n")
        git(self.d, "commit", "-qam", "m")
        v, t = self.run_step()
        self.assertEqual(v, "BLOCKED")
        self.assertIn("main repo", t)
        self.assertIn("f", t)
        self.assertEqual(git(self.d, "status", "--porcelain", "--untracked-files=no"), "")
        self.assertEqual(self.count(), 2)

    def test_nested_merged_main_with_origin_untouched(self):
        sub = f"{self.d}/sub"
        seed(sub)
        git(sub, "switch", "-q", "-c", "stage/f")
        Path(sub, "n").write_text("z\n")
        git(sub, "add", "n")
        git(sub, "commit", "-qm", "n")
        git(sub, "switch", "-q", "master")
        git(self.d, "remote", "add", "origin", "/nonexistent.git")
        before = git(self.d, "rev-parse", "master")
        v, t = self.run_step()
        self.assertEqual(v, "DONE", t)
        self.assertEqual(git(self.d, "rev-parse", "master"), before)
        self.assertEqual(self.count(sub), 2)
        self.assertTrue(Path(sub, "n").exists())


class GraphTest(tps.FlowTest):
    def test_step_in_graph_for_local_repo(self):
        seen, r = self.sequence()
        self.assertIn("local-merge", seen)
        self.assertIn("the `local-merge` step already squashed", r["message"])

    def test_no_step_for_fixed_branch_work(self):
        Path(self.d, "pipeline.json").write_text('{"branch_work": "dev", "qualityrun": false, "testrun": false}')
        seen, _ = self.sequence()
        self.assertNotIn("local-merge", seen)

    def test_active_ignores_pr_false(self):
        Path(self.d, "pipeline.json").write_text('{"pr": false}')
        import argparse

        self.assertTrue(pr_mode.local_merge_active(argparse.Namespace(dir=self.d, stage="s")))
