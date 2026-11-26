"""Reviewer dispatch after the retired complexity route (no ccn gate): dry run and plain Haiku reviewer."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import steps
from tests.support.tmpdirs import tracked_mkdtemp


class T(unittest.TestCase):
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
