"""plan_run.run step events: startup, plan, check and plan-review each emit start and done; a plan-fix round emits its pair and the OK line counts it (fake claude session, temp dir)."""

import argparse
import os
import shutil
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import claude, plan_run
from pipeline.configuration import PLAN_REVIEW_REPORT_FILE, WORK_DIR
from pipeline.ctx import Ctx
from pipeline.run import Run
from pipeline.util import read_jsonl
from tests.support.tmpdirs import tracked_mkdtemp


class PlanRunStepsTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.pf = os.path.join(self.d, "p")
        with open(self.pf, "w") as f:
            f.write("goal")
        self.folder = os.path.join(self.d, WORK_DIR, "f")
        os.makedirs(self.folder)
        self.report = os.path.join(self.folder, PLAN_REVIEW_REPORT_FILE)
        self.printed = []
        self.metrics = Ctx(Run(stage="f", dir=self.d)).metrics

    def fake_claude(self, args, cwd, timeout=None):
        if any(x.startswith("Review the plan") for x in args):
            with open(self.report, "w") as f:
                f.write("VERDICT: DONE\n")
        return {**dict.fromkeys(claude.SUMS, 0), "error": False, "result": "VERDICT: DONE"}

    def run_plan(self, testrun_problems):
        args = argparse.Namespace(dir=self.d, feature="f", prompt_file=self.pf, dry_run=False, model="sonnet")
        with (
            mock.patch.object(claude, "session", side_effect=self.fake_claude),
            mock.patch.object(plan_run, "code_context", return_value=""),
            mock.patch.object(plan_run, "check_now", return_value=(3, [])),
            mock.patch.object(plan_run, "testrun_problems", side_effect=testrun_problems),
            mock.patch("builtins.print", side_effect=lambda *x, **k: self.printed.append(" ".join(map(str, x)))),
        ):
            return plan_run.run(args)

    def plan_steps(self):
        return [(e["step"], e["state"]) for e in read_jsonl(self.metrics) if e.get("ev") == "plan-step"]

    def test_steps_start_and_done_and_no_fix_round(self):
        self.assertEqual(self.run_plan(lambda a: []), 0)
        pairs = self.plan_steps()
        for step in ("startup", "plan", "check", "plan-review"):
            self.assertEqual(pairs.count((step, "start")), 1, step)
            self.assertEqual(pairs.count((step, "done")), 1, step)
        self.assertNotIn("plan-fix", {s for s, _ in pairs})
        self.assertEqual(self.printed[-1], "OK 3 tasks (plan-fix rounds: 0)")

    def test_plan_fix_round_emits_pair_and_is_counted(self):
        # testrun rejects one Test line once: testrun_fix runs one plan-fix session, then the check passes again
        self.assertEqual(self.run_plan([["Test line rejected"], []]), 0)
        pairs = self.plan_steps()
        self.assertEqual(pairs.count(("plan-fix", "start")), 1)
        self.assertEqual(pairs.count(("plan-fix", "done")), 1)
        fix = [e for e in read_jsonl(self.metrics) if e.get("ev") == "session" and e.get("id") == "plan-fix"]
        self.assertEqual(len(fix), 1)
        self.assertEqual(self.printed[-1], "OK 3 tasks (plan-fix rounds: 1)")


if __name__ == "__main__":
    unittest.main()
