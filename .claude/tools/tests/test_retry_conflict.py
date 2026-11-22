"""orch retry whose rebase conflicts: the step is recorded BLOCKED with the conflicting files and the resolve command; no retry is written and the job branch is left as it was."""

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
from pipeline import cli_orch, worktree  # noqa: E402
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402


def sh(repo, *cmd):
    return subprocess.run(["git", "-C", repo, *cmd], check=True, capture_output=True, text=True).stdout.strip()


class RetryConflictTest(unittest.TestCase):
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
        self.put(self.m, "f", "x")
        with open(f"{self.t}/.work/s/conflict-step.md", "w") as f:
            f.write("Predicted paths: src/x")
        self.info = worktree.create(self.t, "s", "conflict-step")
        self.addCleanup(worktree.remove, self.t, "s", "conflict-step", True)

    def put(self, repo, name, text):
        with open(f"{repo}/{name}", "w") as f:
            f.write(text)
        sh(repo, "add", name)
        sh(repo, "commit", "-qm", name)

    def make_conflict(self):
        self.put(self.info["root"], "f", "mine")
        self.put(self.m, "f", "theirs")

    def retry(self):
        a = argparse.Namespace(cmd="retry", stage="s", dir=self.t, step="conflict-step", note="", from_step="", addendum="")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as cm:
            cli_orch.cmd_reset(a)
        return str(cm.exception)

    def log(self):
        with open(f"{self.t}/.work/s/orch.jsonl", encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def test_conflict_records_blocked(self):
        self.make_conflict()
        msg = self.retry()
        self.assertIn("BLOCKED", msg)
        rep = [e for e in self.log() if e["ev"] == "report"]
        self.assertEqual(len(rep), 1)
        self.assertEqual(rep[0]["verdict"], "BLOCKED")
        self.assertEqual(rep[0]["task"], "conflict-step")
        self.assertIn("conflicts in f", rep[0]["reason"])
        self.assertIn(f"git -C {self.info['root']} rebase", rep[0]["reason"])
        with open(rep[0]["file"], encoding="utf-8") as f:
            self.assertTrue(f.readline().startswith("VERDICT: BLOCKED"))
        self.assertFalse([e for e in self.log() if e["ev"] == "retry"])

    def test_conflict_leaves_job_clean(self):
        self.make_conflict()
        self.retry()
        root = self.info["root"]
        self.assertEqual(sh(root, "status", "--porcelain"), "")
        self.assertFalse(os.path.exists(sh(root, "rev-parse", "--path-format=absolute", "--git-path", "rebase-merge")))
