import json
import os
import subprocess
import sys
import unittest
import unittest.mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
from pipeline import worktree  # noqa: E402
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402


def sh(repo, *cmd):
    return subprocess.run(["git", "-C", repo, *cmd], check=True, capture_output=True, text=True).stdout.strip()


class RefreshTest(unittest.TestCase):
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
        with open(f"{self.t}/.work/s/task-1.md", "w") as f:
            f.write("Predicted paths: src/x")
        self.info = worktree.create(self.t, "s", "task-1")
        self.addCleanup(worktree.remove, self.t, "s", "task-1", True)

    def put(self, repo, name, text):
        with open(f"{repo}/{name}", "w") as f:
            f.write(text)
        sh(repo, "add", name)
        sh(repo, "commit", "-qm", name)

    def test_rebases_onto_moved_base(self):
        root = self.info["root"]
        self.put(root, "own", "1")
        self.put(self.m, "later", "2")  # a merge landed on the stage branch after dispatch
        res = worktree.refresh(self.t, "s", "task-1")
        self.assertEqual(res["rebased"], ["main"])
        self.assertTrue(os.path.isfile(f"{root}/later") and os.path.isfile(f"{root}/own"))

    def test_conflict_aborts(self):
        root = self.info["root"]
        self.put(root, "f", "mine")
        self.put(self.m, "f", "theirs")
        with self.assertRaisesRegex(RuntimeError, "conflicts"):
            worktree.refresh(self.t, "s", "task-1")
        self.assertEqual(sh(root, "status", "--porcelain"), "")
        self.assertFalse(os.path.exists(sh(root, "rev-parse", "--path-format=absolute", "--git-path", "rebase-merge")))

    def test_discard_dirty_when_blocked(self):
        root = self.info["root"]
        with open(f"{root}/junk", "w") as f:
            f.write("z")
        with open(f"{root}/f", "w") as f:
            f.write("changed")
        worktree.refresh(self.t, "s", "task-1")  # not blocked: kept
        self.assertTrue(os.path.isfile(f"{root}/junk"))
        res = worktree.refresh(self.t, "s", "task-1", discard=True)
        self.assertEqual(len(res["discarded"]), 2)
        self.assertEqual(sh(root, "status", "--porcelain"), "")


if __name__ == "__main__":
    unittest.main()
