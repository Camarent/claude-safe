import json
import os
import subprocess
import sys
import unittest
import unittest.mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import wt  # noqa: E402
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402


def sh(*cmd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *cmd], check=True, capture_output=True)


def sh_out(repo, *cmd):
    return subprocess.run(["git", "-C", repo, *cmd], check=True, capture_output=True, text=True).stdout.strip()


class MainRestoreTest(unittest.TestCase):
    """merge() with the main checkout on another branch: switch it back to the stage branch, log once, go on. A dirty tree still blocks."""

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
        r = wt.create(self.t, "s", "job-a")
        open(f"{r['root']}/g", "w").write("g")
        wt.commit(self.t, "s", "job-a", "feat(x): add g")
        sh("-C", self.m, "checkout", "-q", "-b", "release")

    def _events(self):
        path = f"{self.t}/.work/s/orch.jsonl"
        if not os.path.isfile(path):
            return []
        return [json.loads(ln) for ln in open(path) if ln.strip()]

    def test_merge_switches_main_back_logs_once_and_continues(self):  # C1
        self.assertEqual(wt.merge(self.t, "s", "job-a")["."], "merged")
        self.assertEqual(sh_out(self.m, "branch", "--show-current"), "dev")
        self.assertEqual(sh_out(self.m, "log", "--format=%s", "-1"), "feat(x): add g")
        restored = [e for e in self._events() if e.get("ev") == "main_restored"]
        self.assertEqual(len(restored), 1)
        self.assertEqual((restored[0]["repo"], restored[0]["from"], restored[0]["to"]), (".", "release", "dev"))

    def test_dirty_main_checkout_still_blocks(self):  # C2
        open(f"{self.m}/f", "w").write("dirty")
        with self.assertRaisesRegex(wt.Blocked, "main checkout .* has uncommitted changes"):
            wt.merge(self.t, "s", "job-a")
        self.assertEqual(sh_out(self.m, "branch", "--show-current"), "release")
        self.assertEqual([e for e in self._events() if e.get("ev") == "main_restored"], [])


if __name__ == "__main__":
    unittest.main()
