"""plan_run.run gaps files follow the final plan-review pass: GAPS.md holds exactly its gaps, and a clean pass removes GAPS.md and the stale PLAN_REVIEW_GAPS_FILE."""

import argparse
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import claude, plan_run
from pipeline.configuration import EXIT_LIMIT, GAPS_FILE, PLAN_FILE, PLAN_REVIEW_GAPS_FILE, PLAN_REVIEW_REPORT_FILE, WORK_DIR
from tests.support.tmpdirs import tracked_mkdtemp

# one phase row: the phase folder phases/01-alpha is where cli_feature writes the plan-review gaps
PLAN_TEXT = (
    "# plan\n\n| dir | goal | done when | depends | paths | status | tasks |\n|---|---|---|---|---|---|---|\n| 01-alpha | goal | done | | | pending | 1 |\n"
)


class PlanRunGapsFinalTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.pf = os.path.join(self.d, "p")
        with open(self.pf, "w") as f:
            f.write("goal")
        self.folder = os.path.join(self.d, WORK_DIR, "f")
        os.makedirs(self.folder)
        with open(os.path.join(self.folder, PLAN_FILE), "w") as f:
            f.write(PLAN_TEXT)
        self.phase_folder = os.path.join(self.folder, "phases", "01-alpha")
        os.makedirs(self.phase_folder)
        self.report = os.path.join(self.folder, PLAN_REVIEW_REPORT_FILE)
        self.gaps_file = os.path.join(self.folder, GAPS_FILE)
        # cli_feature writes the plan-review gaps into the phase folder, not the feature root
        self.review_gaps_file = os.path.join(self.phase_folder, PLAN_REVIEW_GAPS_FILE)
        self.printed = []

    def args(self):
        return argparse.Namespace(dir=self.d, feature="f", prompt_file=self.pf, dry_run=False, model="sonnet")

    def fake_reviewer(self, report_text):
        def session(args, cwd, timeout=None):
            with open(self.report, "w") as f:
                f.write(report_text)
            return {"result": "", "error": False}

        return session

    def run_plan(self, report_text):
        def plan_session(a, c, model, prompt, tag):
            return "DONE"

        with (
            mock.patch.object(claude, "session", side_effect=self.fake_reviewer(report_text)),
            mock.patch.object(plan_run, "session", side_effect=plan_session),
            mock.patch.object(plan_run, "code_context", return_value=""),
            mock.patch.object(plan_run, "check_now", return_value=(3, [])),
            mock.patch.object(plan_run, "testrun_problems", return_value=[]),
            mock.patch.object(plan_run, "step"),
            mock.patch("builtins.print", side_effect=lambda *x, **k: self.printed.append(" ".join(map(str, x)))),
        ):
            return plan_run.run(self.args())

    def write(self, path, text):
        with open(path, "w") as f:
            f.write(text)

    def read_lines(self, path):
        with open(path) as f:
            return f.read().splitlines()

    def test_clean_final_pass_removes_stale_gaps_file(self):
        self.write(self.gaps_file, "GAPS:\nplan-review: gap a\nplan-review: gap b\nplan-review: gap c\nplan-review: gap d\nplan-review: gap e\n")
        rc = self.run_plan("VERDICT: DONE\n")
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(self.gaps_file))
        self.assertTrue(any(line.startswith("OK ") for line in self.printed))

    def test_clean_final_pass_removes_stale_plan_review_gaps_file(self):
        self.write(self.review_gaps_file, "plan-review: gap old\n")
        rc = self.run_plan("VERDICT: DONE\n")
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(self.review_gaps_file))

    def test_final_pass_with_two_gaps_leaves_exactly_those_two(self):
        self.write(self.gaps_file, "GAPS:\n" + "".join(f"plan-review: gap old {i}\n" for i in range(5)))
        rc = self.run_plan("GAP: task 1 has no test line\nGAP: task 2 has no done line\nVERDICT: FAIL\n")
        self.assertEqual(rc, EXIT_LIMIT)
        self.assertEqual(
            self.read_lines(self.gaps_file),
            ["GAPS:", "plan-review: gap task 1 has no test line", "plan-review: gap task 2 has no done line"],
        )

    def test_final_pass_with_gaps_drops_stale_plan_review_gaps_file(self):
        self.write(self.review_gaps_file, "plan-review: gap old\n")
        rc = self.run_plan("GAP: task 3 has no test line\nVERDICT: FAIL\n")
        self.assertEqual(rc, EXIT_LIMIT)
        self.assertFalse(os.path.exists(self.review_gaps_file))
        self.assertEqual(self.read_lines(self.gaps_file), ["GAPS:", "plan-review: gap task 3 has no test line"])


if __name__ == "__main__":
    unittest.main()
