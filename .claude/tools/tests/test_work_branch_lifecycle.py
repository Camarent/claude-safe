"""ensure_work_branch: create from branch_release, resume, fixed name, no git."""

import argparse
import json
import os
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import branches, cli_feature, cli_wave
from pipeline.configuration.layout import ORCH_LOG, WORK_DIR
from tests.support.gitrepo import git
from tests.support.tmpdirs import tracked_mkdtemp


class EnsureWorkBranchTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        git(self.d, "init", "-q", "-b", "master")
        git(self.d, "commit", "-q", "--allow-empty", "-m", "seed")
        self.a = argparse.Namespace(dir=self.d, dry_run=False)

    def head(self):
        return git(self.d, "symbolic-ref", "--short", "HEAD")

    def test_creates_and_switches_for_feature(self):
        self.assertEqual(branches.ensure_work_branch(self.a, "feat"), "")
        self.assertEqual(self.head(), "stage/feat")

    def test_missing_branch_is_created_from_release(self):
        sha = git(self.d, "rev-parse", "master")
        self.assertEqual(branches.ensure_work_branch(self.a, "s2"), "")
        self.assertEqual(git(self.d, "rev-parse", "refs/heads/stage/s2"), sha)

    def test_stage_name_when_no_feature(self):
        self.assertEqual(branches.ensure_work_branch(self.a, "s1"), "")
        self.assertEqual(self.head(), "stage/s1")

    def test_phase_stage_uses_the_feature_branch(self):
        branches.ensure_work_branch(self.a, "feat/phases/01-a")
        self.assertEqual(self.head(), "stage/feat")

    def test_resume_reuses_existing_branch_and_its_commits(self):
        branches.ensure_work_branch(self.a, "feat")
        git(self.d, "commit", "-q", "--allow-empty", "-m", "work")
        sha = git(self.d, "rev-parse", "HEAD")
        git(self.d, "switch", "-q", "master")
        self.assertEqual(branches.ensure_work_branch(self.a, "feat"), "")
        self.assertEqual((self.head(), git(self.d, "rev-parse", "HEAD")), ("stage/feat", sha))

    def test_released_stage_branch_is_not_recreated(self):
        log_dir = os.path.join(self.d, WORK_DIR, "s1")
        os.makedirs(log_dir)
        with open(os.path.join(log_dir, ORCH_LOG), "w") as f:
            f.write(json.dumps({"ev": "report", "task": "branch-cleanup", "verdict": "DONE"}) + "\n")
        self.assertEqual(branches.ensure_work_branch(self.a, "s1"), "")
        self.assertEqual(self.head(), "master")
        self.assertNotIn("stage/s1", git(self.d, "branch", "--format=%(refname:short)"))

    def test_fixed_name_wins_and_is_created_from_release(self):
        with open(os.path.join(self.d, "pipeline.json"), "w") as f:
            json.dump({"branch_work": "dev"}, f)
        self.assertEqual(branches.ensure_work_branch(self.a, "feat"), "")
        self.assertEqual(self.head(), "dev")

    def test_dirty_checkout_is_refused(self):
        pathlib.Path(self.d, "x.txt").write_text("x")
        self.assertIn("uncommitted changes", branches.dirty_block(self.a))

    def test_dry_run_and_no_git_do_nothing(self):
        self.a.dry_run = True
        self.assertEqual(branches.ensure_work_branch(self.a, "feat"), "")
        self.assertEqual(self.head(), "master")
        # a folder with no git: TMPDIR sits inside the main checkout's repo, so a real non-git temp dir cannot be made; mock the check instead
        with mock.patch.object(branches, "_is_git", return_value=False):
            self.assertEqual(branches.ensure_work_branch(argparse.Namespace(dir=tracked_mkdtemp(), dry_run=False), "feat"), "")


class _Stop(Exception):
    """Raised by a mock to end an entry right after the work-branch step."""


class EntryWorkBranchTest(unittest.TestCase):
    """The kata driver entries (cli_wave._run_guards, cli_feature._feature) reach the real ensure_work_branch: the stage branch exists after them."""

    def setUp(self):
        self.d = tracked_mkdtemp()
        git(self.d, "init", "-q", "-b", "master")
        git(self.d, "commit", "-q", "--allow-empty", "-m", "seed")

    def test_wave_entry_creates_the_stage_branch(self):
        a = argparse.Namespace(dir=self.d, dry_run=False, stage="s3")
        c = argparse.Namespace(w=self.d)
        with (
            mock.patch.object(cli_wave, "_plan_only", return_value=False),
            mock.patch.object(cli_wave, "reset_squash_residue", return_value=[]),
            mock.patch.object(cli_wave, "dirty_block", return_value=None),
            mock.patch.object(cli_wave, "red_base_block", return_value=None),
            mock.patch.object(cli_wave.pr_ops, "sweep_before_branch"),
            mock.patch.object(cli_wave, "recover_unreported", side_effect=_Stop),
            self.assertRaises(_Stop),
        ):
            cli_wave._run_guards(a, c)
        self.assertEqual(git(self.d, "symbolic-ref", "--short", "HEAD"), "stage/s3")

    def test_feature_entry_creates_the_feature_branch(self):
        a = argparse.Namespace(dir=self.d, dry_run=False, feature="feat4")
        with (
            mock.patch("search.strata.preflight"),
            mock.patch.object(cli_feature, "dirty_block", return_value=None),
            mock.patch.object(cli_feature, "red_base_block", return_value=None),
            mock.patch.object(cli_feature.pr_ops, "sweep_before_branch"),
            mock.patch.object(cli_feature.pf, "ensure_finish", side_effect=_Stop),
            self.assertRaises(_Stop),
        ):
            cli_feature._feature(a)
        self.assertEqual(git(self.d, "symbolic-ref", "--short", "HEAD"), "stage/feat4")


if __name__ == "__main__":
    unittest.main()
