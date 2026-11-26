"""maincheck reverts a dev's tracked main-checkout writes after saving their diff to a patch; untracked files and the lead's own dirt are only named."""

import json
import os
import subprocess
import sys
import unittest
import unittest.mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import wt  # noqa: E402
from pipeline import linkguard, maincheck  # noqa: E402
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402


def sh(*cmd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *cmd], check=True, capture_output=True)


class MainCheckRevertTest(unittest.TestCase):
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
            json.dump({"repo": "main", "branch_work": "dev", "worktrees": True}, f)
        sh("-C", self.m, "init", "-q", "-b", "dev")
        for name in ("f", "g"):
            with open(f"{self.m}/{name}", "w") as f:
                f.write("x")
        with open(f"{self.m}/.git/info/exclude", "a") as f:
            f.write(".wt\n.work\n")
        sh("-C", self.m, "add", "f", "g")
        sh("-C", self.m, "commit", "-qm", "i")
        with open(f"{self.t}/.work/s/t1.md", "w") as f:
            f.write("Predicted paths: src/x")
        self.r = wt.create(self.t, "s", "t1")

    def check(self):
        return maincheck.check(self.t, "s", "t1")

    def write(self, name, text):
        with open(f"{self.m}/{name}", "w") as f:
            f.write(text)

    def read(self, name):
        with open(f"{self.m}/{name}") as f:
            return f.read()

    def set_config(self, extra):
        with open(f"{self.t}/pipeline.json", "w") as f:
            json.dump({"repo": "main", "branch_work": "dev", "worktrees": True, **extra}, f)

    def patches(self):
        """The saved write patches in the stage folder (their names carry the task id)."""
        found = []
        for root, _, files in os.walk(f"{self.t}/.work/s"):
            found += [os.path.join(root, n) for n in files if n.startswith("mainwrite-t1-")]
        return found

    def test_tracked_edit_is_reverted_and_its_patch_kept(self):
        maincheck.snapshot(self.t, "s", "t1")
        self.write("f", "edited")
        note = self.check()
        self.assertTrue(maincheck.dirt(note))
        self.assertEqual(self.read("f"), "x")  # reverted to HEAD
        self.assertEqual(self.check(), "")  # the reverted tree is clean: nothing left to report
        (patch,) = self.patches()
        self.assertIn(patch, note)  # the lead finds the saved diff from the note
        with open(patch) as f:
            self.assertIn("+edited", f.read())

    def test_gone_tracked_file_is_restored_from_head(self):
        maincheck.snapshot(self.t, "s", "t1")
        os.remove(f"{self.m}/g")
        self.assertIn("g (mtime gone", self.check())
        self.assertEqual(self.read("g"), "x")
        self.assertEqual(len(self.patches()), 1)

    def test_untracked_new_file_is_kept_and_named(self):
        maincheck.snapshot(self.t, "s", "t1")
        self.write("draft.py", "first draft")
        note = self.check()
        self.assertIn("draft.py", note)
        self.assertIn(maincheck.MAIN_KEPT_STATE, note)
        self.assertEqual(self.read("draft.py"), "first draft")  # new files are never reverted or deleted
        self.assertEqual(self.patches(), [])

    def test_dirt_at_the_snapshot_is_never_reverted(self):
        self.write("f", "lead edit")
        maincheck.snapshot(self.t, "s", "t1")
        self.write("f", "dev edit")
        note = self.check()
        self.assertIn("f (mtime", note)
        self.assertEqual(self.read("f"), "dev edit")  # the lead's own work stays
        self.assertEqual(self.patches(), [])

    def test_switch_off_keeps_the_write_and_names_it(self):
        self.set_config({"main_guard_revert": False})
        maincheck.snapshot(self.t, "s", "t1")
        self.write("f", "edited")
        note = self.check()
        self.assertIn("f (mtime", note)
        self.assertIn(maincheck.MAIN_KEPT_STATE, note)
        self.assertEqual(self.read("f"), "edited")
        self.assertEqual(self.patches(), [])

    def test_patch_save_failure_means_no_revert(self):
        maincheck.snapshot(self.t, "s", "t1")
        self.write("f", "edited")
        with unittest.mock.patch.object(linkguard, "_saved", return_value=False):
            note = self.check()
        self.assertIn("NOT reverted", note)
        self.assertEqual(self.read("f"), "edited")  # no saved patch, so nothing is reverted
        self.assertEqual(self.patches(), [])


if __name__ == "__main__":
    unittest.main()
