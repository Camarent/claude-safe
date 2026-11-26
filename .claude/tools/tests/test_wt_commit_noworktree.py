import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
import unittest.mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)  # tools dir must be on sys.path before the imports below
import wt  # noqa: E402 - tools dir must be on sys.path first
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402 - tools dir must be on sys.path first


def sh(*cmd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *cmd], check=True, capture_output=True)


class CommitWithoutWorktree(unittest.TestCase):
    """`wt.py commit` for a task with no job worktree commits nothing and exits 3 (Blocked)."""

    def setUp(self):
        ident = {f"GIT_{w}_{k}": v for w in ("AUTHOR", "COMMITTER") for k, v in (("NAME", "t"), ("EMAIL", "t@t"))}
        p = unittest.mock.patch.dict(os.environ, ident)
        p.start()
        self.addCleanup(p.stop)
        self.t = tracked_mkdtemp()
        self.m = f"{self.t}/main"
        os.makedirs(f"{self.m}/workspace/c")
        os.makedirs(f"{self.t}/.work/s")
        with open(f"{self.t}/pipeline.json", "w") as f:
            json.dump({"repo": "main", "branch_work": "dev", "nested_repo_dir_prefix": "workspace/"}, f)
        for r in (self.m, f"{self.m}/workspace/c"):
            sh("-C", r, "init", "-q", "-b", "dev")
            with open(f"{r}/f", "w") as f:
                f.write("x")
            sh("-C", r, "add", "f")
            sh("-C", r, "commit", "-qm", "i")
        with open(f"{self.t}/.work/s/task-1.md", "w") as f:
            f.write("Predicted paths: src/x")

    def run_commit(self, task):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            wt.main(["commit", "--stage", "s", "--task", task, "--subject", "feat(x): add m", "--dir", self.t])
        return cm.exception.code, buf.getvalue(), err.getvalue()

    def test_task_without_worktree_exits_3(self):
        code, out, err = self.run_commit("task-9")
        self.assertEqual(code, 3)
        self.assertEqual(json.loads(out), {})
        self.assertIn("nothing was committed", err)

    def test_task_with_worktree_still_exits_0(self):
        r = wt.create(self.t, "s", "task-1")
        with open(f"{r['root']}/m", "w") as f:
            f.write("m")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            wt.main(["commit", "--stage", "s", "--task", "task-1", "--subject", "feat(x): add m", "--dir", self.t])
        self.assertEqual(list(json.loads(buf.getvalue())), ["."])
