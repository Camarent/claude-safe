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
    def dispatch(self, env):
        a = mock.Mock(dir=tracked_mkdtemp(), stage="s")
        with (
            mock.patch.dict(os.environ, env),
            mock.patch("pipeline.steps.review_ccn_hit", return_value="ccn 14 > 10") as hit,
            mock.patch("pipeline.steps.prompt_for", return_value="p"),
        ):
            d = steps._disp_reviewer(a, {"id": "t1"}, "r1", mock.Mock(reviewer_sonnet=False))
        return d, hit

    def test_dry_env_skips_gate_and_stays_haiku(self):
        d, hit = self.dispatch({steps.ENV_DRY_NO_CCN: "1"})
        hit.assert_not_called()
        self.assertEqual(d["model"], "haiku")
        self.assertNotIn("why", d)

    def test_gate_runs_without_env(self):
        env = {k: v for k, v in os.environ.items() if k != steps.ENV_DRY_NO_CCN}
        with mock.patch.dict(os.environ, env, clear=True):
            d, hit = self.dispatch({})
        hit.assert_called_once()
        self.assertEqual(d["model"], "sonnet")


if __name__ == "__main__":
    unittest.main()
