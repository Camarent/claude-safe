"""orch retry whose job rebase conflicts: a normal block (SystemExit, no traceback) that names the resolve command; the step is recorded BLOCKED and no retry event is written."""

import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_orch, worktree
from tests.support.tmpdirs import tracked_mkdtemp


def sh(repo, *cmd):
    return subprocess.run(["git", "-C", repo, *cmd], check=True, capture_output=True, text=True).stdout.strip()


class RetryRebaseBlockTest(unittest.TestCase):
    def setUp(self):
        ident = {f"GIT_{w}_{k}": v for w in ("AUTHOR", "COMMITTER") for k, v in (("NAME", "t"), ("EMAIL", "t@t"))}
        p = mock.patch.dict(os.environ, ident)
        p.start()
        self.addCleanup(p.stop)
        self.t = tracked_mkdtemp()
        self.m = os.path.join(self.t, "main")
        os.makedirs(self.m)
        os.makedirs(os.path.join(self.t, ".work", "s"))
        with open(os.path.join(self.t, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump({"repo": "main", "branch_work": "dev"}, f)
        sh(self.m, "init", "-q", "-b", "dev")
        self.put(self.m, "f", "x")
        with open(os.path.join(self.t, ".work", "s", "rebase-step.md"), "w", encoding="utf-8") as f:
            f.write("Predicted paths: f")
        self.info = worktree.create(self.t, "s", "rebase-step")
        self.addCleanup(worktree.remove, self.t, "s", "rebase-step", True)

    def put(self, repo, name, text):
        with open(os.path.join(repo, name), "w", encoding="utf-8") as f:
            f.write(text)
        sh(repo, "add", name)
        sh(repo, "commit", "-qm", name)

    def make_conflict(self):
        self.put(self.info["root"], "f", "mine")
        self.put(self.m, "f", "theirs")

    def retry(self):
        a = argparse.Namespace(cmd="retry", stage="s", dir=self.t, step="rebase-step", note="", from_step="", addendum="")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            cli_orch.cmd_reset(a)
        return str(cm.exception)

    def log(self):
        with open(os.path.join(self.t, ".work", "s", "orch.jsonl"), encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def test_conflict_message_names_resolve_command(self):
        self.make_conflict()
        msg = self.retry()
        root = self.info["root"]
        self.assertIn("BLOCKED", msg)
        self.assertIn(f"resolve: git -C {root} rebase {self.info['base']}", msg)
        self.assertIn("git rebase --continue; then retry rebase-step", msg)

    def test_conflict_writes_no_retry_event(self):
        self.make_conflict()
        self.retry()
        self.assertFalse([e for e in self.log() if e["ev"] == "retry"])
        rep = [e for e in self.log() if e["ev"] == "report"]
        self.assertEqual(len(rep), 1)
        self.assertEqual(rep[0]["verdict"], "BLOCKED")

    def test_conflict_leaves_job_clean(self):
        self.make_conflict()
        self.retry()
        root = self.info["root"]
        self.assertEqual(sh(root, "status", "--porcelain"), "")
        self.assertFalse(os.path.exists(sh(root, "rev-parse", "--path-format=absolute", "--git-path", "rebase-merge")))


if __name__ == "__main__":
    unittest.main()
