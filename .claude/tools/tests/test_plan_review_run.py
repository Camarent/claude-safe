"""plan_run.run plan review: phase review on the big route (Plan.md), one-phase review on the small route, one fix round on FAIL, dry-run does no review."""

import argparse
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import claude, plan_run
from pipeline.configuration import PLAN_FILE, PLAN_REVIEW_MODEL_HEAVY, PLAN_REVIEW_MODEL_LIGHT, PLAN_REVIEW_REPORT_FILE, WORK_DIR
from tests.support.tmpdirs import tracked_mkdtemp


class PlanReviewRunTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.pf = os.path.join(self.d, "p")
        with open(self.pf, "w") as f:
            f.write("goal")
        self.folder = os.path.join(self.d, WORK_DIR, "f")
        os.makedirs(self.folder)
        self.report = os.path.join(self.folder, PLAN_REVIEW_REPORT_FILE)
        self.reviews = []  # (model, prompt) per reviewer session
        self.tags = []  # plan_run.session tags, in order
        self.fix_prompts = []  # prompts of the plan_run.session calls, in order
        self.printed = []

    def args(self, dry_run=False):
        return argparse.Namespace(dir=self.d, feature="f", prompt_file=self.pf, dry_run=dry_run, model="sonnet")

    def make_plan_md(self):
        with open(os.path.join(self.folder, PLAN_FILE), "w") as f:
            f.write("# plan\n")

    def fake_reviewer(self, report_text, error=None):
        def session(args, cwd, timeout=None):
            if error:
                raise error
            prompt = next(x for x in args if x.startswith("Review the plan"))
            self.reviews.append((args[args.index("--model") + 1], prompt))
            with open(self.report, "w") as f:
                f.write(report_text)
            return {"result": "", "error": False}

        return session

    def run_plan(self, dry_run=False, n_tasks=3):
        def plan_session(a, c, model, prompt, tag):
            self.tags.append(tag)
            self.fix_prompts.append(prompt)
            return "DONE"

        with (
            mock.patch.object(plan_run, "session", side_effect=plan_session),
            mock.patch.object(plan_run, "code_context", return_value=""),
            mock.patch.object(plan_run, "check_now", return_value=(n_tasks, [])),
            mock.patch.object(plan_run, "testrun_problems", return_value=[]),
            mock.patch.object(plan_run, "step"),
            mock.patch("builtins.print", side_effect=lambda *x, **k: self.printed.append(" ".join(map(str, x)))),
        ):
            return plan_run.run(self.args(dry_run))

    def test_big_route_done_one_reviewer_call_no_fix(self):
        self.make_plan_md()
        with mock.patch.object(claude, "session", side_effect=self.fake_reviewer("VERDICT: DONE\n")):
            rc = self.run_plan()
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.reviews), 1)
        self.assertEqual(self.reviews[0][0], PLAN_REVIEW_MODEL_LIGHT)
        self.assertNotIn("plan-fix", self.tags)

    def test_big_route_fail_runs_one_fix_and_prints_gaps(self):
        self.make_plan_md()
        report = "GAP: task 3 has no test line\nVERDICT: FAIL\n"
        with mock.patch.object(claude, "session", side_effect=self.fake_reviewer(report)):
            rc = self.run_plan()
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.reviews), 1)
        self.assertEqual(self.tags, ["plan", "plan-fix"])
        self.assertIn(self.report, self.fix_prompts[-1])
        self.assertIn("plan-review: gap task 3 has no test line", "\n".join(self.printed).splitlines())

    def test_one_phase_seven_tasks_is_light_eight_is_heavy(self):
        for n, model in ((7, PLAN_REVIEW_MODEL_LIGHT), (8, PLAN_REVIEW_MODEL_HEAVY)):
            self.reviews.clear()
            with mock.patch.object(claude, "session", side_effect=self.fake_reviewer("VERDICT: DONE\n")):
                rc = self.run_plan(n_tasks=n)
            self.assertEqual(rc, 0)
            self.assertEqual(self.reviews[0][0], model, f"{n} tasks")

    def test_big_route_eight_tasks_is_light_because_phase_review(self):
        # with Plan.md the review is the phase kind: its model does not depend on the task count
        self.make_plan_md()
        with mock.patch.object(claude, "session", side_effect=self.fake_reviewer("VERDICT: DONE\n")):
            rc = self.run_plan(n_tasks=8)
        self.assertEqual(rc, 0)
        self.assertEqual(self.reviews[0][0], PLAN_REVIEW_MODEL_LIGHT)

    def test_reviewer_error_keeps_plan_valid(self):
        self.make_plan_md()
        with mock.patch.object(claude, "session", side_effect=self.fake_reviewer("", error=RuntimeError("boom"))):
            rc = self.run_plan()
        self.assertEqual(rc, 0)
        self.assertNotIn("plan-fix", self.tags)

    def test_dry_run_does_no_review(self):
        self.make_plan_md()
        with mock.patch.object(claude, "session", side_effect=self.fake_reviewer("VERDICT: FAIL\nGAP: x\n")) as sess:
            rc = self.run_plan(dry_run=True)
        self.assertEqual(rc, 0)
        sess.assert_not_called()
        self.assertEqual(self.tags, [])


if __name__ == "__main__":
    unittest.main()
