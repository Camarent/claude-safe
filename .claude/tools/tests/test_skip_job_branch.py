"""orch skip on a task whose dev committed in its job worktree: refuse while the work is not on the work branch, skip cleanly otherwise."""

import argparse
import json
import os
import subprocess
import sys
import unittest
import unittest.mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import wt  # noqa: E402
from pipeline import cli_orch  # noqa: E402
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402


def sh(*cmd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *cmd], check=True, capture_output=True)


class SkipJobBranchTest(unittest.TestCase):
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
        open(f"{self.m}/f", "w").write("x")
        sh("-C", self.m, "add", "f")
        sh("-C", self.m, "commit", "-qm", "i")
        open(f"{self.t}/.work/s/task-1.md", "w").write("Predicted paths: g")
        open(f"{self.t}/.work/s/task-2.md", "w").write("Predicted paths: h")

    def skip(self, task):
        a = argparse.Namespace(cmd="skip", stage="s", dir=self.t, step=task, note="", from_step="")
        cli_orch.cmd_reset(a)

    def log(self):
        if not os.path.exists(f"{self.t}/.work/s/orch.jsonl"):
            return []
        with open(f"{self.t}/.work/s/orch.jsonl", encoding="utf-8") as f:
            return [json.loads(x) for x in f if x.strip()]

    def commit_in_job(self, task, name):
        root = wt.create(self.t, "s", task)["root"]
        open(f"{root}/{name}", "w").write("y")
        sh("-C", root, "add", name)
        sh("-C", root, "commit", "-qm", f"{task} work")
        return root

    def test_skip_refuses_when_job_branch_has_work_not_on_work_branch(self):
        self.commit_in_job("task-1", "g")
        with self.assertRaises(SystemExit) as cm:
            self.skip("task-1")
        msg = str(cm.exception)
        self.assertIn("refused", msg)
        self.assertIn("wt.py merge --stage s --task task-1", msg)
        self.assertIn("wt.py remove --force --stage s --task task-1", msg)  # review audit M5: discard path named too
        self.assertFalse([e for e in self.log() if e["ev"] == "skip"])  # nothing recorded

    def test_skip_refuses_on_uncommitted_job_work(self):
        root = wt.create(self.t, "s", "task-1")["root"]
        open(f"{root}/g", "w").write("half")
        with self.assertRaises(SystemExit):
            self.skip("task-1")

    def test_skip_clean_when_no_commits_ahead(self):
        wt.create(self.t, "s", "task-2")
        self.skip("task-2")
        self.assertEqual([e["task"] for e in self.log() if e["ev"] == "skip"], ["task-2"])

    def test_skip_clean_after_the_work_is_merged(self):
        self.commit_in_job("task-1", "g")
        wt.merge(self.t, "s", "task-1")
        self.skip("task-1")
        self.assertEqual([e["task"] for e in self.log() if e["ev"] == "skip"], ["task-1"])

    def test_skip_without_a_job_worktree_is_unchanged(self):
        self.skip("master-merged")
        self.assertEqual([e["task"] for e in self.log() if e["ev"] == "skip"], ["master-merged"])


if __name__ == "__main__":
    unittest.main()
