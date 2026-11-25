"""Reviewer complexity gate (pipeline.ccn): synthetic temp git repo, lizard present or mocked away."""

import os
import shutil
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import ccn, steps
from tests.support.tmpdirs import tracked_mkdtemp

HIGH = "def f(x):\n" + "".join(f"    if x == {i}:\n        return {i}\n" for i in range(14)) + "    return 0\n"
LOW = "def g(x):\n    return x\n"


def sh(d, *a):
    subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True, capture_output=True)


class T(unittest.TestCase):
    def repo(self, body):
        d = tracked_mkdtemp()
        sh(d, "init", "-q", "-b", "main")
        open(os.path.join(d, "m.py"), "w").write("# start\n")
        sh(d, "add", "."), sh(d, "commit", "-qm", "a")
        sh(d, "checkout", "-qb", "job")
        open(os.path.join(d, "m.py"), "w").write(body)
        sh(d, "commit", "-qam", "b")
        return d

    @unittest.skipUnless(shutil.which("lizard"), "lizard not installed (baked into the container image)")
    def test_high(self):
        hit = ccn.worst_changed(self.repo(HIGH), "main", "job", 10)
        self.assertEqual(hit[1:], ("f", "m.py"))
        self.assertGreater(hit[0], 10)

    def test_low(self):
        self.assertIsNone(ccn.worst_changed(self.repo(LOW), "main", "job", 10))

    def test_unchanged_function_ignored(self):
        d = self.repo(HIGH)
        open(os.path.join(d, "m.py"), "a").write(LOW)
        sh(d, "commit", "-qam", "c")
        sh(d, "checkout", "-q", "main")
        sh(d, "merge", "-q", "job")
        sh(d, "checkout", "-qb", "job2")
        open(os.path.join(d, "m.py"), "a").write("\n# tail\n")
        sh(d, "commit", "-qam", "d")
        self.assertIsNone(ccn.worst_changed(d, "main", "job2", 10))

    def test_lizard_missing(self):
        d = self.repo(HIGH)
        with mock.patch("pipeline.ccn.subprocess.run", side_effect=FileNotFoundError):
            self.assertIsNone(ccn.worst_changed(d, "main", "job", 10))

    def test_no_worktree_no_hit(self):
        a = mock.Mock(dir=tracked_mkdtemp(), stage="s")
        self.assertIsNone(ccn.review_ccn_hit(a, "t1"))

    def test_dry_run_reviewer_never_sonnet_by_ccn(self):
        # dry-run walks a scratch copy of the stage (no worktrees): the gate runs in the orch subprocess and finds no job, so no ccn reason
        from tests.support.project import dry_run, make_project

        _rc, steps, _ = dry_run(make_project())
        reviewers = [s for s in steps if s[1] == "reviewer"]
        self.assertTrue(reviewers)
        self.assertEqual({s[2] for s in reviewers}, {"haiku"})
        self.assertNotIn("ccn", " ".join(s[3] for s in steps))


class DryGate(unittest.TestCase):
    def test_reviewer_dispatch_has_no_ccn_gate_with_or_without_dry_env(self):
        # the ccn route is gone from steps: a reviewer with no events is Haiku with no why, dry run or not
        self.assertFalse(hasattr(steps, "review_ccn_hit"))
        for env in ({steps.ENV_DRY_NO_CCN: "1"}, {}):
            a = mock.Mock(dir=tracked_mkdtemp(), stage="s")
            with mock.patch.dict(os.environ, env), mock.patch("pipeline.steps.prompt_for", return_value="p"):
                d = steps._disp_reviewer(a, {"id": "t1"}, "r1")
            self.assertEqual((d["model"], d.get("why")), ("haiku", None))


if __name__ == "__main__":
    unittest.main()
