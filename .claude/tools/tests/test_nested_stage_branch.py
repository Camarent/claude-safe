"""Stage branch in nested (local-only) repos: ensure, job base, cleanup."""

import argparse
import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import wt
from pipeline import branches, phases, pr_ops
from tests.support.gitrepo import git


class NestedTest(unittest.TestCase):
    def setUp(self):
        ident = {f"GIT_{w}_{k}": v for w in ("AUTHOR", "COMMITTER") for k, v in (("NAME", "t"), ("EMAIL", "t@t"))}
        p = unittest.mock.patch.dict(os.environ, ident)
        p.start()
        self.addCleanup(p.stop)
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        # Fake gh first on PATH: the real one needs network and auth. It answers "no PRs" and succeeds.
        fake = f"{self.d}-bin"
        os.makedirs(fake)
        self.addCleanup(shutil.rmtree, fake, ignore_errors=True)
        with open(f"{fake}/gh", "w") as f:
            f.write("#!/bin/sh\necho '[]'\n")
        os.chmod(f"{fake}/gh", 0o755)
        pp = unittest.mock.patch.dict(os.environ, {"PATH": f"{fake}{os.pathsep}{os.environ['PATH']}"})
        pp.start()
        self.addCleanup(pp.stop)
        self.c = f"{self.d}/workspace/c"
        os.makedirs(self.c)
        self.write_cfg({})
        for r in (self.d, self.c):
            git(r, "init", "-q", "-b", "master")
            open(f"{r}/f", "w").write("x")
            git(r, "add", "f")
            git(r, "commit", "-qm", "i")
        with open(f"{self.d}/.git/info/exclude", "a") as ex:
            ex.write(".work/\n.wt/\npipeline.json\nworkspace/\n")
        os.makedirs(f"{self.d}/.work/s")
        open(f"{self.d}/.work/s/task-1.md", "w").write("Predicted paths: workspace/c/f")
        self.a = argparse.Namespace(dir=self.d, dry_run=False, stage="s")

    def write_cfg(self, cfg):
        json.dump(cfg, open(f"{self.d}/pipeline.json", "w"))

    def head(self, r):
        return git(r, "symbolic-ref", "--short", "HEAD")

    def test_ensure_creates_in_both_and_resumes(self):
        self.assertEqual(branches.ensure_work_branch(self.a, "feat"), "")
        self.assertEqual((self.head(self.d), self.head(self.c)), ("stage/feat", "stage/feat"))
        self.assertEqual(branches.ensure_work_branch(self.a, "feat"), "")
        self.assertEqual(self.head(self.c), "stage/feat")

    def test_ensure_blocks_naming_nested_repo(self):
        git(self.c, "switch", "-q", "-c", "stage/feat")
        open(f"{self.c}/f", "w").write("other")
        git(self.c, "commit", "-qam", "diverge")
        git(self.c, "switch", "-q", "master")
        git(self.c, "branch", "-m", "stage/feat", "keep")
        git(self.c, "branch", "stage/feat", "keep")
        open(f"{self.c}/f", "w").write("dirty")  # switching would overwrite this
        msg = branches.ensure_work_branch(self.a, "feat")
        self.assertTrue(msg.startswith("blocked: nested repo workspace/c: cannot switch"), msg)

    def test_job_branch_cut_from_stage_and_merge_squashes_into_it(self):
        branches.ensure_work_branch(self.a, "feat")
        self.write_cfg({"repo": ".", "branch_work": "stage/feat"})
        r = wt.create(self.d, "s", "task-1")
        self.assertEqual(git(self.c, "rev-parse", f"{r['branch']}~0"), git(self.c, "rev-parse", "stage/feat"))
        wroot = r["repos"]["workspace/c"]
        open(f"{wroot}/f", "w").write("new")
        git(wroot, "commit", "-qam", "job")
        wt.merge(self.d, "s", "task-1")
        self.assertEqual(git(self.c, "show", "stage/feat:f"), "new")
        self.assertEqual(git(self.c, "show", "master:f"), "x")

    def test_base_branch_falls_back_to_head_without_work_branch(self):
        self.assertEqual(wt._base_branch(self.c, "stage/none"), "HEAD")

    def test_cleanup_blocked_then_deleted_and_idempotent(self):
        self.write_cfg({})
        branches.ensure_work_branch(self.a, "feat")
        open(f"{self.c}/f", "w").write("n")
        git(self.c, "commit", "-qam", "work")
        open(f"{self.d}/g", "w").write("g")
        git(self.d, "add", "g")
        git(self.d, "commit", "-qm", "gw")
        v, text = pr_ops.run("cleanup", self.d, "s", "feat")
        self.assertEqual(v, "BLOCKED")
        for r in (self.d, self.c):
            git(r, "switch", "-q", "master")
            git(r, "merge", "--squash", "stage/feat", check=False)
            git(r, "commit", "-qm", "squash", check=False)
            git(r, "switch", "-q", "stage/feat")
        v, text = pr_ops.run("cleanup", self.d, "s", "feat")
        self.assertEqual(v, "DONE", text)
        for r in (self.d, self.c):
            self.assertEqual(git(r, "branch", "--list", "stage/feat"), "")
            self.assertEqual(self.head(r), "master")
        self.assertEqual(pr_ops.run("cleanup", self.d, "s", "feat")[0], "DONE")

    def test_cleanup_blocks_on_nested_only_work(self):
        branches.ensure_work_branch(self.a, "feat")
        open(f"{self.c}/only", "w").write("1")
        git(self.c, "add", "only")
        git(self.c, "commit", "-qm", "only here")
        git(self.d, "switch", "-q", "master")
        git(self.d, "branch", "-D", "stage/feat")
        v, text = pr_ops.run("cleanup", self.d, "s", "feat")
        self.assertEqual(v, "BLOCKED")
        self.assertIn("workspace/c", text)
        self.assertNotEqual(git(self.c, "branch", "--list", "stage/feat"), "")

    def test_release_message_lists_nested_repos(self):
        branches.ensure_work_branch(self.a, "feat")
        cfg = __import__("pipeline.configuration", fromlist=["project"]).project(self.d)
        self.assertIn("local-merge", phases._release_message(argparse.Namespace(dir=self.d, stage="feat", dry_run=False), cfg, "feat"))

    def test_plain_repo_and_fixed_branch(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        git(d, "init", "-q", "-b", "master")
        git(d, "commit", "-q", "--allow-empty", "-m", "s")
        a = argparse.Namespace(dir=d, dry_run=False, stage="s")
        self.assertEqual(branches.ensure_work_branch(a, "feat"), "")
        self.assertEqual(self.head(d), "stage/feat")
        json.dump({"branch_work": "dev"}, open(f"{d}/pipeline.json", "w"))
        git(d, "add", "-A")
        git(d, "commit", "-qm", "cfg")
        self.assertEqual(branches.ensure_work_branch(a, "other"), "")
        self.assertEqual(self.head(d), "dev")


if __name__ == "__main__":
    unittest.main()
