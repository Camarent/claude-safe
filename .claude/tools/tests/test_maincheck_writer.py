"""maincheck: the guard log names the job that wrote a main-checkout path, so each job is blamed only for its own writes."""

import json
import os
import subprocess
import sys
import unittest
import unittest.mock

TOOLS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, TOOLS)
import wt  # noqa: E402
from pipeline import maincheck  # noqa: E402
from pipeline.configuration import GUARD_LOG_FILE  # noqa: E402
from tests.support.tmpdirs import tracked_mkdtemp  # noqa: E402


def sh(*cmd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *cmd], check=True, capture_output=True)


class MainCheckWriterTest(unittest.TestCase):
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
        with open(f"{self.m}/f", "w") as f:
            f.write("x")
        with open(f"{self.m}/.git/info/exclude", "a") as f:
            f.write(".wt\n.work\n")
        sh("-C", self.m, "add", "f")
        sh("-C", self.m, "commit", "-qm", "i")
        for task in ("t1", "t2"):
            with open(f"{self.t}/.work/s/{task}.md", "w") as f:
                f.write("Predicted paths: src/x")
            wt.create(self.t, "s", task)
        maincheck.snapshot(self.t, "s", "t1")
        maincheck.snapshot(self.t, "s", "t2")

    def log(self, *entries):
        with open(f"{self.t}/.work/s/{GUARD_LOG_FILE}", "w") as f:
            for job, path in entries:
                f.write(json.dumps({"ts": 0, "job": job, "tool": "Write", "path": path}) + "\n")

    def write_main(self, name):
        with open(f"{self.m}/{name}", "w") as f:
            f.write("draft")

    def test_writer_is_blamed_and_named(self):
        self.log(("t1", "draft.py"))
        self.write_main("draft.py")
        note = maincheck.check(self.t, "s", "t1")
        self.assertIn("draft.py", note)
        self.assertIn("written by job t1", note)

    def test_other_job_is_not_blamed(self):
        self.log(("t1", "draft.py"))
        self.write_main("draft.py")
        self.assertEqual(maincheck.check(self.t, "s", "t2"), "")

    def test_absolute_log_path_is_attributed(self):
        self.log(("t1", f"{self.m}/draft.py"))
        self.write_main("draft.py")
        self.assertEqual(maincheck.check(self.t, "s", "t2"), "")
        self.assertIn("written by job t1", maincheck.check(self.t, "s", "t1"))

    def test_no_log_blames_every_job_as_before(self):
        self.write_main("draft.py")
        self.assertIn("draft.py", maincheck.check(self.t, "s", "t1"))
        self.assertIn("draft.py", maincheck.check(self.t, "s", "t2"))
        self.assertNotIn("written by job", maincheck.check(self.t, "s", "t2"))

    def test_unlogged_path_stays_with_every_job(self):
        self.log(("t1", "draft.py"))
        self.write_main("draft.py")
        self.write_main("other.py")
        self.assertIn("other.py", maincheck.check(self.t, "s", "t2"))
        self.assertNotIn("draft.py", maincheck.check(self.t, "s", "t2"))

    def test_read_writers_skips_bad_lines_and_keeps_first_writer(self):
        with open(f"{self.t}/.work/s/{GUARD_LOG_FILE}", "w") as f:
            f.write("not json\n[1, 2]\n" + json.dumps({"job": "t1", "path": "a"}) + "\n")
            f.write(json.dumps({"job": "t2", "path": "a"}) + "\n" + json.dumps({"job": "t2", "path": "b"}) + "\n")
        self.assertEqual(maincheck.read_writers(self.t, "s"), {"a": "t1", "b": "t2"})

    def test_read_writers_without_log_is_empty(self):
        self.assertEqual(maincheck.read_writers(self.t, "s"), {})
