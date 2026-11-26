import argparse
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import configuration, phases, pr_base, pr_mode, pr_ops, tasks
from tests.support.gitrepo import git

IDENT = {f"GIT_{w}_{k}": v for w in ("AUTHOR", "COMMITTER") for k, v in (("NAME", "t"), ("EMAIL", "t@t"))}


class Base(unittest.TestCase):
    def setUp(self):
        p = mock.patch.dict(os.environ, {**IDENT, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"})
        p.start()
        self.addCleanup(p.stop)
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.root = t.name

    def repo(self, name, branch, commit=True):
        d = os.path.join(self.root, name)
        os.makedirs(d, exist_ok=True)
        git(d, "init", "-q", "-b", branch)
        if commit:
            Path(d, "f").write_text("x\n")
            git(d, "add", "-A")
            git(d, "commit", "-qm", "base")
        return d


class ReleaseBranch(Base):
    def test_main_and_master(self):
        for b in ("main", "master"):
            d = self.repo(b, b)
            self.assertEqual(configuration.release_branch(d, {}), b)

    def test_explicit_wins(self):
        d = self.repo("m", "main")
        self.assertEqual(configuration.release_branch(d, {"branch_release": "trunk"}), "trunk")

    def test_origin_head_wins(self):
        d = self.repo("m", "main")
        git(d, "branch", "dev")
        git(d, "update-ref", "refs/remotes/origin/dev", "HEAD")
        git(d, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/dev")
        self.assertEqual(configuration.release_branch(d, {}), "dev")

    def test_init_default_branch_then_current(self):
        d = self.repo("m", "trunk")
        self.assertEqual(configuration.release_branch(d, {}), "trunk")
        git(d, "branch", "alt")
        git(d, "config", "init.defaultBranch", "alt")
        self.assertEqual(configuration.release_branch(d, {}), "alt")

    def test_head_on_stage_branch_keeps_release(self):
        d = self.repo("m", "develop")
        git(d, "checkout", "-qb", "stage/x")
        self.assertEqual(configuration.release_branch(d, {}), "develop")
        self.assertEqual(configuration.project(d)["branch_release"], "develop")

    def test_head_on_stage_branch_ambiguous_returns_loud_default(self):  # review2 fixes (1): project() must not raise
        d = self.repo("m", "develop")
        git(d, "branch", "other")
        git(d, "checkout", "-qb", "job/s-t")
        self.assertEqual(configuration.release_branch(d, {}), "master")
        self.assertEqual(configuration.project(d)["branch_release"], "master")

    def test_not_git(self):
        self.assertEqual(configuration.release_branch(self.root, {}), "master")

    def test_fallback_is_flagged(self):  # review3 fixes (4)
        d = self.repo("m", "develop")
        git(d, "branch", "other")
        git(d, "checkout", "-qb", "job/s-t")
        for path in (self.root, d):
            self.assertEqual(configuration.release_guess(path, {}), ("master", True))
        d2 = self.repo("n", "main")
        self.assertEqual(configuration.release_guess(d2, {}), ("main", False))

    def test_project_detects_main_repo(self):
        d = self.repo("m", "main")
        self.assertEqual(configuration.project(d)["branch_release"], "main")

    def test_nested_repo_own_branch(self):
        d = self.repo("p", "master")
        n = self.repo("p/sub", "main")
        cfg = configuration.project(d)
        self.assertEqual(configuration.release_branch(d, cfg), "master")
        self.assertEqual(configuration.release_branch(n, cfg), "main")


class LocalMergeMainRepo(Base):
    def test_main_repo_and_nested_other_branch(self):
        d = self.repo("p", "main")
        n = self.repo("p/sub", "master")
        Path(d, ".gitignore").write_text("sub/\n")
        git(d, "add", "-A")
        git(d, "commit", "-qm", "ign")
        for r in (d, n):
            git(r, "checkout", "-qb", "stage/s")
            Path(r, "g").write_text("y\n")
            git(r, "add", "-A")
            git(r, "commit", "-qm", "feat")
        v, text = pr_ops.run("local-merge", d, "s", "s")
        self.assertEqual(v, "DONE", text)
        self.assertEqual(git(d, "show", "main:g"), "y")
        self.assertEqual(git(n, "show", "master:g"), "y")


HOOK = """#!/usr/bin/env bash
case "$1" in
  current) if [ -n "${2:-}" ]; then git show "$2:VERSION"; else cat VERSION; fi ;;
esac
"""


class NoGhWithOrigin(Base):
    """A repo with a bare origin but no gh on PATH: local-merge squashes, release is BLOCKED naming gh auth, cleanup does not block."""

    def setUp(self):
        super().setUp()
        bindir = os.path.join(self.root, "bin")
        os.makedirs(bindir)
        for tool in ("git", "bash", "cat", "sed", "grep", "env"):
            os.symlink(subprocess.run(["which", tool], capture_output=True, text=True, check=True).stdout.strip(), os.path.join(bindir, tool))
        p = mock.patch.dict(os.environ, {"PATH": bindir})
        p.start()
        self.addCleanup(p.stop)
        self.d = self.repo("w", "main", commit=False)
        Path(self.d, "hook.sh").write_text(HOOK)
        Path(self.d, "VERSION").write_text("0.1.5\n")
        Path(self.d, "pipeline.json").write_text('{"release_script": "hook.sh", "pr": false}')  # explicit opt-out of the PR flow: merged and pushed directly
        git(self.d, "add", "-A")
        git(self.d, "commit", "-qm", "base")
        bare = os.path.join(self.root, "o.git")
        subprocess.run(["git", "init", "-q", "--bare", bare], check=True, capture_output=True)
        git(self.d, "remote", "add", "origin", bare)
        git(self.d, "push", "-q", "origin", "main")
        git(self.d, "checkout", "-qb", "stage/s")
        Path(self.d, "g").write_text("y\n")
        git(self.d, "add", "-A")
        git(self.d, "commit", "-qm", "feat")

    def test_pr_inactive(self):
        self.assertFalse(pr_mode.pr_active(argparse.Namespace(dir=self.d, stage="s")))

    def test_gh_flicker_does_not_squash_locally(self):
        Path(self.d, "pipeline.json").write_text('{"release_script": "hook.sh"}')  # origin, `pr` unset: the PR flow, gh just failed
        a = argparse.Namespace(dir=self.d, stage="s")
        self.assertFalse(pr_mode.direct_flow(a))
        before = git(self.d, "rev-parse", "main")
        v, text = pr_ops.run("local-merge", self.d, "s", "s")
        self.assertEqual((v, git(self.d, "rev-parse", "main")), ("DONE", before), text)

    def test_local_merge_release_blocked_cleanup(self):
        v, text = pr_ops.run("local-merge", self.d, "s", "s")
        self.assertEqual((v, git(self.d, "show", "main:g")), ("DONE", "y"), text)
        v, text = pr_ops.run("release", self.d, "s", "s")
        self.assertEqual(v, "BLOCKED", text)
        self.assertIn("gh auth", text)
        v, text = pr_ops.run("cleanup", self.d, "s", "s")
        self.assertEqual(v, "DONE", text)

    def test_local_only_repo_never_calls_gh(self):
        git(self.d, "remote", "remove", "origin")
        git(self.d, "checkout", "-q", "main")
        git(self.d, "merge", "-q", "--squash", "stage/s")
        git(self.d, "commit", "-qm", "sq")
        with mock.patch.object(pr_base, "_view", side_effect=AssertionError("gh called")):
            v, text = pr_ops.run("cleanup", self.d, "s", "s")
        self.assertEqual(v, "DONE", text)


class PhaseSteps(NoGhWithOrigin):
    """Origin but no gh: the PR flow is off. Steps are derived for a `main` repo."""

    def steps(self, extra=""):
        os.makedirs(os.path.join(self.d, ".work", "s"), exist_ok=True)
        tm = os.path.join(self.d, ".work", "s", "TASKS.md")
        Path(tm).write_text(extra)
        flags = {**tasks.parse_flags(tm), "audit": False, "test": False, "index": False}
        a = argparse.Namespace(stage="s", dir=self.d)
        return {st["id"]: st for g in phases.final_phases(a, flags, [], []) for st in g}

    def test_push_master_uses_the_release_branch(self):
        st = self.steps()
        self.assertIn("push-master", st)
        msg = st["push-master"]["prompt"] if "prompt" in st["push-master"] else str(st["push-master"])
        self.assertNotIn("push origin master", msg)
        self.assertIn("`main`", msg)

    def test_gh_down_with_origin_pauses_the_lead_to_merge_by_hand(self):  # review2 fixes (4)
        Path(self.d, "pipeline.json").write_text('{"release_script": "hook.sh"}')
        st = self.steps("PRE-APPROVED: master merge\n")
        self.assertTrue(st["master-merged"]["pause"])
        self.assertIn("`gh` is missing", st["master-merged"]["prompt"] if "prompt" in st["master-merged"] else str(st["master-merged"]))
        self.assertNotIn("push-master", st)

    def test_fixed_branch_work_still_pushes(self):  # review2 fixes (4)
        Path(self.d, "pipeline.json").write_text('{"release_script": "hook.sh", "branch_work": "stage/fixed"}')
        self.assertIn("push-master", self.steps())

    def test_no_origin_no_push_master_step(self):
        git(self.d, "remote", "remove", "origin")
        self.assertNotIn("push-master", self.steps())

    def test_pre_approved_keeps_merge_release_cleanup_steps(self):
        st = self.steps("PRE-APPROVED: master merge\n")
        for sid in ("local-merge", "release", "branch-cleanup"):
            self.assertIn(sid, st)
        self.assertNotIn("master-merged", st)
        self.assertIn("master-merged", self.steps())

    def test_no_origin_release_tags_without_gh(self):
        git(self.d, "remote", "remove", "origin")
        pr_ops.run("local-merge", self.d, "s", "s")
        v, text = pr_ops.run("release", self.d, "s", "s")
        self.assertEqual(v, "DONE", text)
        self.assertEqual(git(self.d, "tag"), "v0.1.5")
