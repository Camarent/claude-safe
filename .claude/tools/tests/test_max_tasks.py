"""Synthetic tests for the max-tasks-per-stage limit: plan check, runner stop, override, fix tasks exempt."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_plan, plan_check
from tests.support.orch_harness import OrchHarness
from tests.support.tmpdirs import tracked_mkdtemp


def table(n):
    return (
        "| task | what | owner | price | review |\n|---|---|---|---|---|\n"
        + "".join(f"| task-{i} | x | dev | 1 | wave 1, haiku |\n" for i in range(1, n + 1))
        + "\n## Graph\nwave 1: "
        + ", ".join(f"task-{i}" for i in range(1, n + 1))
        + "\nflags: no-merge no-test no-audit no-retro\n"
    )


class PlanCheckLimit(unittest.TestCase):
    def stage(self, n, cfg=None):
        d = tracked_mkdtemp()
        if cfg is not None:
            with open(os.path.join(d, "pipeline.json"), "w") as f:
                f.write(cfg)
        cli_plan.main(["stage-init", "--stage", "S", "--goal", "g", "--dir", d])
        for i in range(1, n + 1):
            cli_plan.main(["task", "--stage", "S", "--id", str(i), "--title", f"t{i}", "--paths", f"src/f{i}.py", "--test", "true", "--done", "ok", "--dir", d])
        return os.path.join(d, ".work", "S")

    def test_31_tasks_fail_30_pass_and_the_limit_is_configurable(self):
        def probs(s):
            return [p for p in plan_check.check_stage(s)[1] if "limit" in p]

        self.assertEqual(probs(self.stage(30)), [])
        p = probs(self.stage(31))
        self.assertEqual(len(p), 1)
        self.assertIn("phases", p[0])
        self.assertEqual(probs(self.stage(31, '{"max_tasks": 40}')), [])

    def test_plan_check_counts_what_the_runner_counts(self):
        s = self.stage(1)
        with open(os.path.join(s, "TASKS.md"), "w") as f:
            f.write(table(31))  # table rows are not plan.py bullets, but the runner counts them
        self.assertTrue([p for p in plan_check.check_stage(s)[1] if "limit is 30" in p])

    def test_started_tasks_do_not_count(self):
        s = self.stage(31)
        with open(os.path.join(s, "orch.jsonl"), "w") as f:
            f.write('{"ev": "dispatch", "task": "task-1", "id": "a1"}\n')
        self.assertEqual([p for p in plan_check.check_stage(s)[1] if "limit" in p], [])

    def test_shared_file_warns_for_three_tasks_not_two_and_keeps_errors_empty(self):
        def run(n):
            d = tracked_mkdtemp()
            cli_plan.main(["stage-init", "--stage", "S", "--goal", "g", "--dir", d])
            for i in range(1, n + 1):
                dep = ["--depends", str(i - 1)] if i > 1 else []
                cli_plan.main(
                    [
                        "task",
                        "--stage",
                        "S",
                        "--id",
                        str(i),
                        "--title",
                        f"t{i}",
                        "--paths",
                        f"web/i18n/en.json,src/f{i}.py",
                        "--test",
                        "true",
                        "--done",
                        "ok",
                        "--dir",
                        d,
                        *dep,
                    ]
                )
            return plan_check.check_stage(os.path.join(d, ".work", "S"))[1]

        got = [p for p in run(3) if p.startswith("shared:")]
        self.assertEqual(len(got), 1)
        self.assertIn("web/i18n/en.json is listed by task-1, task-2, task-3 (3 tasks, 3 waves)", got[0])
        self.assertEqual([p for p in run(3) if p.startswith("shared:")] and plan_check.errors_of(got), [])
        self.assertEqual([p for p in run(2) if p.startswith("shared:")], [])


class RunnerLimit(OrchHarness):
    def setUp(self):
        super().setUp()
        with open(f"{self.d}/.work/s/TASKS.md", "w") as f:
            f.write(table(31))

    def test_above_the_limit_the_lead_is_asked(self):
        r = self.go("next")
        self.assertEqual(r["action"], "ask_main")
        self.assertIn("limit is 30", r["message"])

    def test_a_raised_limit_runs(self):
        self.config(max_tasks=40)
        self.assertEqual(self.go("next")["action"], "dispatch")

    def test_fix_tasks_of_a_fix_round_come_on_top(self):
        with open(f"{self.d}/.work/s/orch.jsonl", "a") as f:
            f.write('{"ev": "report", "id": "a99", "task": "fix-plan-1", "role": "architect", "verdict": "DONE", "reason": "", "file": "x"}\n')
        self.assertEqual(self.go("next")["action"], "dispatch")


if __name__ == "__main__":
    unittest.main()
