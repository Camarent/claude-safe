"""plan_phase wiring of the plan review: a task review per phase, the cover review on the last phase with tasks. The architect and the reviewer are stubbed."""

import os
import sys
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_feature, cli_wave

os.environ["PIPELINE_NO_LEADS"] = "1"  # no first code search (local.py find) in the driver runs
from pipeline import plan_files as pf
from pipeline.configuration import PLAN_EXTRA_TASKS_ALLOWED, PLAN_REVIEW_GAPS_FILE, PLAN_REVIEW_MODEL_HEAVY, PLAN_REVIEW_MODEL_LIGHT, PLAN_REVIEW_REPORT_FILE
from tests.isolated import isolate_cwd
from tests.support.tmpdirs import tracked_mkdtemp

MAX_TASKS = 30
TASKS_NOW = 7


def phases(*dirs):
    return [{"dir": d, "goal": d, "done_when": "true", "depends": [], "paths": [f"src/{d}"], "status": "open", "tasks": 0} for d in dirs]


class PlanReviewFeature(unittest.TestCase):
    def setUp(self):
        isolate_cwd(self)
        self.d = tracked_mkdtemp()
        self.a = cli_wave.argparse.Namespace(feature="F", dir=self.d)
        self.folder = os.path.join(self.d, ".work", "F", "phases", "02-b")
        os.makedirs(self.folder)
        with open(os.path.join(self.folder, "TASKS.md"), "w", encoding="utf-8") as t:
            t.write("# Stage\n")
        self.calls = []  # (agent, model) per claude.session call
        self.reports = []  # report text the reviewer writes, one per reviewer session

    def run_plan_phase(self, dirs, reports):
        """Run plan_phase for phase 02-b among DIRS; REPORTS are written by the reviewer in order. -> (problems, stdout)."""
        self.reports = list(reports)

        def fake_session(args, *_, **__):
            agent = args[args.index("--agent") + 1]
            model = args[args.index("--model") + 1] if "--model" in args else ""
            self.calls.append((agent, model))
            if agent == "plan-reviewer" and self.reports:
                with open(os.path.join(self.folder, PLAN_REVIEW_REPORT_FILE), "w", encoding="utf-8") as f:
                    f.write(self.reports.pop(0))
            return {}

        phase = next(p for p in phases(*dirs) if p["dir"] == "02-b")
        out = StringIO()
        with (
            mock.patch.object(pf, "read_phases", return_value=phases(*dirs)),
            mock.patch.object(cli_feature, "project", return_value={"max_tasks": MAX_TASKS}),
            mock.patch.object(cli_feature, "phase_ctx"),
            mock.patch.object(cli_feature.claude, "session", side_effect=fake_session),
            mock.patch.object(cli_feature.plan_check, "check_stage", return_value=(TASKS_NOW, [])),
            redirect_stdout(out),
        ):
            probs = cli_feature.plan_phase(self.a, phase)
        self.calls = self.calls[1:]  # the first session is the architect that plans the phase; the tests check the review calls
        return probs, out.getvalue()

    def test_normal_phase_done_runs_one_light_task_review(self):
        probs, _ = self.run_plan_phase(["01-a", "02-b", "03-c"], ["VERDICT: DONE\n"])
        self.assertEqual(probs, [])
        self.assertEqual(self.calls, [("plan-reviewer", PLAN_REVIEW_MODEL_LIGHT)])
        self.assertFalse(os.path.exists(os.path.join(self.folder, PLAN_REVIEW_GAPS_FILE)))

    def test_normal_phase_fail_runs_one_architect_fix_round(self):
        probs, out = self.run_plan_phase(["01-a", "02-b", "03-c"], ["GAP: task-1 has no Test line\nVERDICT: FAIL\n", "VERDICT: DONE\n"])
        self.assertEqual(probs, [])
        self.assertEqual(self.calls, [("plan-reviewer", PLAN_REVIEW_MODEL_LIGHT), ("architect", "")])  # one fix round, no second review
        self.assertIn("plan-review task: 1 gap(s) left", out)
        with open(os.path.join(self.folder, PLAN_REVIEW_GAPS_FILE), encoding="utf-8") as f:
            self.assertEqual(f.read(), "GAP: task-1 has no Test line\n")

    def test_last_phase_done_runs_one_heavy_cover_review(self):
        probs, _ = self.run_plan_phase(["01-a", "02-b", "03-finish"], ["GAP-TASKS: 0\nVERDICT: DONE\n"])
        self.assertEqual(probs, [])
        self.assertEqual(self.calls, [("plan-reviewer", PLAN_REVIEW_MODEL_HEAVY)])

    def test_last_phase_fail_runs_architect_fix_round(self):
        probs, _ = self.run_plan_phase(["01-a", "02-b", "03-finish"], ["GAP: request item 4 has no task\nGAP-TASKS: 2\nVERDICT: FAIL\n"])
        self.assertEqual(probs, [])
        self.assertEqual(self.calls[:2], [("plan-reviewer", PLAN_REVIEW_MODEL_HEAVY), ("architect", "")])

    def test_last_phase_at_limit_plus_extra_is_not_blocked(self):
        gap = MAX_TASKS + PLAN_EXTRA_TASKS_ALLOWED - TASKS_NOW
        probs, _ = self.run_plan_phase(["01-a", "02-b", "03-finish"], [f"GAP-TASKS: {gap}\nVERDICT: FAIL\n"])
        self.assertEqual(probs, [])
        self.assertEqual(self.calls, [("plan-reviewer", PLAN_REVIEW_MODEL_HEAVY), ("architect", "")])

    def test_last_phase_one_task_over_limit_plus_extra_is_blocked(self):
        gap = MAX_TASKS + PLAN_EXTRA_TASKS_ALLOWED - TASKS_NOW + 1
        probs, _ = self.run_plan_phase(["01-a", "02-b", "03-finish"], [f"GAP: request item 9 has no task\nGAP-TASKS: {gap}\nVERDICT: FAIL\n"])
        self.assertEqual(len(probs), 1)
        self.assertTrue(probs[0].startswith("plan-review BLOCKED: another phase needed: "), probs)
        self.assertIn("GAP: request item 9 has no task", probs[0])
        self.assertEqual(self.calls, [("plan-reviewer", PLAN_REVIEW_MODEL_HEAVY)])


if __name__ == "__main__":
    unittest.main()
