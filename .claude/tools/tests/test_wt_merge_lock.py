"""wt.py merge honours the stage driver lock by the pid the lock file holds (STAGE_LOCK)."""

import json
import os
import subprocess
import sys
import unittest
import unittest.mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import wt  # noqa: E402
from pipeline import worktree  # noqa: E402
from pipeline.configuration import STAGE_LOCK  # noqa: E402
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402

T1 = "task-1"


def sh(*cmd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *cmd], check=True, capture_output=True)


class DriverLockMerge(unittest.TestCase):
    def setUp(self):
        ident = {f"GIT_{w}_{k}": v for w in ("AUTHOR", "COMMITTER") for k, v in (("NAME", "t"), ("EMAIL", "t@t"))}
        p = unittest.mock.patch.dict(os.environ, ident)
        p.start()
        self.addCleanup(p.stop)
        self.t = tracked_mkdtemp()
        self.m = f"{self.t}/main"
        os.makedirs(self.m)
        os.makedirs(f"{self.t}/.work/s")
        json.dump({"repo": "main", "branch_work": "dev", "nested_repo_dir_prefix": "workspace/"}, open(f"{self.t}/pipeline.json", "w"))
        sh("-C", self.m, "init", "-q", "-b", "dev")
        open(f"{self.m}/f", "w").write("x")
        sh("-C", self.m, "add", "f")
        sh("-C", self.m, "commit", "-qm", "i")
        open(f"{self.t}/.work/s/{T1}.md", "w").write("Predicted paths: f")
        wt.create(self.t, "s", T1)
        self.lock = f"{self.t}/.work/s/{STAGE_LOCK}"

    def write_holder(self, pid):
        open(self.lock, "w").write(f"{pid}\n")

    def dead_pid(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        return child.pid

    def test_free_lock_lets_merge_proceed(self):
        self.assertEqual(wt.merge(self.t, "s", T1), {".": "nothing"})

    def test_own_parent_pid_lets_merge_proceed(self):  # the driver runs `wt.py merge` as its child
        self.write_holder(os.getppid())
        self.assertEqual(wt.merge(self.t, "s", T1), {".": "nothing"})

    def test_dead_holder_pid_counts_as_free(self):
        self.write_holder(self.dead_pid())
        self.assertEqual(wt.merge(self.t, "s", T1), {".": "nothing"})

    def test_live_holder_blocks_merge_and_names_its_pid(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        self.addCleanup(lambda: (child.kill(), child.wait()))
        self.write_holder(child.pid)
        with self.assertRaises(worktree.Blocked) as ctx:
            wt.merge(self.t, "s", T1)
        self.assertIn(f"pid {child.pid}", str(ctx.exception))
        self.assertTrue(os.path.isdir(f"{self.t}/.wt/s/{T1}"))  # refused before any repo changed


if __name__ == "__main__":
    unittest.main()
