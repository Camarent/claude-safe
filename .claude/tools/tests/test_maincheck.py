"""maincheck: a dev that writes to the main checkout instead of its job worktree is named, nothing is deleted."""

import json
import os
import subprocess
import sys
import unittest
import unittest.mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import testrun  # noqa: E402
import wt  # noqa: E402
from pipeline import maincheck  # noqa: E402
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402


def sh(*cmd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *cmd], check=True, capture_output=True)


class MainCheckTest(unittest.TestCase):
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

    def set_config(self, extra):
        with open(f"{self.t}/pipeline.json", "w") as f:
            json.dump({"repo": "main", "branch_work": "dev", "worktrees": True, **extra}, f)

    def test_clean_main_is_no_finding(self):
        maincheck.snapshot(self.t, "s", "t1")
        self.assertEqual(self.check(), "")

    def test_new_and_changed_files_are_named_and_kept(self):
        maincheck.snapshot(self.t, "s", "t1")
        self.write("draft.py", "first draft")
        self.write("f", "edited")
        note = self.check()
        self.assertIn("MAIN checkout", note)
        self.assertIn("draft.py", note)
        self.assertIn(", f", note)
        self.assertEqual(open(f"{self.m}/draft.py").read(), "first draft")  # report and block, never delete
        self.assertEqual(open(f"{self.m}/f").read(), "edited")
        self.assertIn("draft.py", self.check())  # still reported until the files are gone

    def test_dirt_that_was_there_at_the_start_is_not_the_devs(self):
        self.write("lead-note", "lead")
        maincheck.snapshot(self.t, "s", "t1")
        self.assertEqual(self.check(), "")
        self.write("lead-note", "lead changed again")
        self.assertIn("lead-note", self.check())

    def test_cleaned_up_main_passes_again(self):
        maincheck.snapshot(self.t, "s", "t1")
        self.write("draft.py", "x")
        self.assertIn("draft.py", self.check())
        os.remove(f"{self.m}/draft.py")
        self.assertEqual(self.check(), "")

    def test_pipeline_folders_and_a_merge_in_progress_are_ignored(self):
        maincheck.snapshot(self.t, "s", "t1")
        os.makedirs(f"{self.m}/.work/x", exist_ok=True)
        self.write(".work/x/y", "z")
        self.assertEqual(self.check(), "")
        self.write("g", "squashed in")
        with open(f"{self.m}/.git/SQUASH_MSG", "w") as f:
            f.write("Squashed commit")
        self.assertEqual(self.check(), "")

    def test_ignored_glob(self):
        maincheck.snapshot(self.t, "s", "t1")
        self.write("qrun-1.log", "run output")
        self.assertEqual(self.check(), "")

    def test_ignored_glob_setting_overrides_the_default(self):
        self.set_config({"main_guard_ignore_globs": []})
        maincheck.snapshot(self.t, "s", "t1")
        self.write("qrun-1.log", "run output")
        self.assertIn("qrun-1.log", self.check())

    def test_note_has_mtime_and_job_start(self):
        maincheck.snapshot(self.t, "s", "t1")
        self.write("draft.py", "x")
        note = self.check()
        self.assertTrue(maincheck.dirt(note))
        self.assertIn("draft.py (mtime ", note)
        self.assertIn("job start ", note)
        self.assertIn("MAIN checkout", note)

    def test_gone_tracked_file_shows_gone_mtime(self):
        maincheck.snapshot(self.t, "s", "t1")
        os.remove(f"{self.m}/g")  # a tracked file deleted: git status still lists it
        self.assertIn("g (mtime gone", self.check())

    def test_dirt_recognises_only_main_notes(self):
        self.assertFalse(maincheck.dirt("tests failed: 2"))
        self.assertFalse(maincheck.dirt(""))

    def test_no_snapshot_means_no_verdict(self):
        self.write("draft.py", "x")
        self.assertEqual(self.check(), "")

    def test_testrun_fails_a_dev_with_main_writes(self):
        maincheck.snapshot(self.t, "s", "t1")
        self.write("draft.py", "x")
        self.assertIn("draft.py", testrun.outside_writes(self.t, "s", "t1"))


if __name__ == "__main__":
    unittest.main()
