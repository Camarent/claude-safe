"""orch retry --pick: the job branch is rebuilt from the stage tip with the picked own commits, in the given order; the other commits are gone and the event is logged with `picked`."""

import argparse
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
from pipeline import cli_orch, worktree  # noqa: E402  # sys.path set on the line above, before the pipeline import
from pipeline.configuration import PICK_BACKUP_REF  # noqa: E402  # sys.path set on the line above, before the pipeline import
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402  # sys.path set on the line above, before the pipeline import


def sh(repo, *cmd):
    return subprocess.run(["git", "-C", repo, *cmd], check=True, capture_output=True, text=True).stdout.strip()


class RetryPickTest(unittest.TestCase):
    def setUp(self):
        ident = {f"GIT_{w}_{k}": v for w in ("AUTHOR", "COMMITTER") for k, v in (("NAME", "t"), ("EMAIL", "t@t"))}
        p = unittest.mock.patch.dict(os.environ, ident)
        p.start()
        self.addCleanup(p.stop)
        self.t = tracked_mkdtemp()
        self.m = f"{self.t}/main"
        os.makedirs(self.m)
        os.makedirs(f"{self.t}/.work/s")
        with open(f"{self.t}/pipeline.json", "w") as f:
            json.dump({"repo": "main", "branch_work": "dev"}, f)
        sh(self.m, "init", "-q", "-b", "dev")
        self.base_sha = self.put(self.m, "f", "x")
        with open(f"{self.t}/.work/s/pick-step.md", "w") as f:
            f.write("Predicted paths: src/x")
        self.info = worktree.create(self.t, "s", "pick-step")
        self.addCleanup(worktree.remove, self.t, "s", "pick-step", True)
        self.root = self.info["root"]
        # three own commits of the job; "notes.md" stands for a doc-only commit made for a gate
        self.c1 = self.put(self.root, "f1", "one")
        self.c2 = self.put(self.root, "notes.md", "doc")
        self.c3 = self.put(self.root, "f3", "three")

    def put(self, repo, name, text):
        with open(f"{repo}/{name}", "w") as f:
            f.write(text)
        sh(repo, "add", name)
        sh(repo, "commit", "-qm", name)
        return sh(repo, "rev-parse", "HEAD")

    def retry_raw(self, picks):
        a = argparse.Namespace(cmd="retry", stage="s", dir=self.t, step="pick-step", note="", from_step="", addendum="", pick=picks)
        cli_orch.cmd_reset(a)

    def retry(self, picks):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.retry_raw(picks)

    def log(self):
        path = f"{self.t}/.work/s/orch.jsonl"
        if not os.path.exists(path):  # a refused or failed pick writes no event
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def job_subjects(self):
        return sh(self.root, "log", "--reverse", "--format=%s", "dev..HEAD").split()

    def test_pick_keeps_only_picked_commits_in_order(self):
        self.retry([self.c3, self.c1])
        self.assertEqual(self.job_subjects(), ["f3", "f1"])

    def test_unpicked_doc_commit_is_absent(self):
        self.retry([self.c1, self.c3])
        self.assertEqual(self.job_subjects(), ["f1", "f3"])
        self.assertNotIn("notes.md", sh(self.root, "ls-tree", "--name-only", "HEAD").split())

    def test_pick_logs_retry_refresh_with_picked(self):
        self.retry([self.c3, self.c1])
        ev = [e for e in self.log() if e["ev"] == "retry_refresh"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["picked"], [self.c3, self.c1])
        self.assertEqual(ev[0]["task"], "pick-step")

    def test_sha_not_own_commit_is_refused_before_reset(self):
        before = sh(self.root, "rev-parse", "HEAD")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            self.retry_raw([self.base_sha])
        self.assertIn("--pick refused", str(cm.exception))
        self.assertEqual(sh(self.root, "rev-parse", "HEAD"), before)
        self.assertEqual(sh(self.root, "for-each-ref", "--format=%(refname)", PICK_BACKUP_REF), "")
        self.assertFalse([e for e in self.log() if e["ev"] == "retry_refresh"])

    def test_failed_pick_is_aborted_and_old_tip_kept_as_backup(self):
        # "mine" then "second" on top of it: picking only the second onto dev (f = "x") conflicts
        self.put(self.root, "f", "mine")
        c = self.put(self.root, "f", "second")
        old = sh(self.root, "rev-parse", "HEAD")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            self.retry_raw([c])
        msg = str(cm.exception)
        self.assertIn("cherry-pick of", msg)
        self.assertIn(old, msg)
        self.assertEqual(sh(self.root, "rev-parse", "--verify", f"{PICK_BACKUP_REF}/{old}"), old)
        cherry = sh(self.root, "rev-parse", "--path-format=absolute", "--git-path", "CHERRY_PICK_HEAD")
        self.assertFalse(os.path.exists(cherry))
        self.assertEqual(sh(self.root, "status", "--porcelain"), "")
        self.assertFalse([e for e in self.log() if e["ev"] == "retry"])
