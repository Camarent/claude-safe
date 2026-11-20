"""Synthetic tests that pin the order of a stage's steps (`wave.py run --dry-run`) for made-up projects."""

import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.support.project import dry_run, make_project, roles

BASE = ["dev", "script", "script", "reviewer", "merger"]  # scripts = the task Test lines, then the advisory quality run, after dev DONE
AUDITS = ["sec-auditor", "arch-auditor"]


class StageSequenceTest(unittest.TestCase):
    def test_minimal_stage_runs_task_then_merge_test_and_audits(self):
        rc, steps, last = dry_run(make_project("no-retro"))
        self.assertEqual(rc, 0)
        r = roles(steps)
        self.assertEqual(r[:5], BASE)
        self.assertIn("tester", r)
        self.assertEqual(r[-2:], AUDITS)
        self.assertIn("merge", last)  # the master merge is the lead's call

    def test_no_audit_flag_removes_both_audits(self):
        _, steps, _ = dry_run(make_project("no-retro no-audit"))
        self.assertFalse(set(AUDITS) & set(roles(steps)))

    def test_ui_flag_adds_the_ux_review(self):
        _, steps, _ = dry_run(make_project("no-retro ui", audit_md="# audit\n\n```ux\nbuild: true\nserve: true\nurl: http://x\n```\n"))
        self.assertIn("ux-reviewer", roles(steps))

    def test_pre_approved_master_merge_skips_the_pause(self):
        _, _, last = dry_run(make_project("no-retro", extra_lines="PRE-APPROVED: master merge\n"))
        self.assertNotIn("merge `dev` into `master`", last)

    def test_gates_in_config_turns_final_test_into_a_script_step(self):
        _, steps, _ = dry_run(make_project("no-retro", config={"gates": [{"name": "t", "cmd": "true"}]}))
        scripts = [s for s in steps if s[1] == "script"]
        self.assertTrue(any("final_check.py" in s[3] for s in scripts), scripts)

    def test_without_gates_the_tester_is_a_model_step(self):
        _, steps, _ = dry_run(make_project("no-retro"))
        self.assertFalse(any("final_check.py" in s[3] for s in steps))

    def test_steps_never_name_a_fixed_project_path(self):
        d = make_project("no-retro")
        _, steps, _ = dry_run(d)
        for s in steps:
            self.assertIsNone(re.search(r"(?<![\w.-])/workspace\b", s[3]), s[3])  # a standalone /workspace, not ~/workspace/x
            self.assertNotIn("/root/.claude", s[3])


class ReportTypeSequenceTest(unittest.TestCase):
    def test_report_task_skips_quality_run_and_goes_to_sonnet_reviewer(self):
        from tests.test_report_tasks import two_task_project

        _, steps, _ = dry_run(two_task_project())
        self.assertEqual([s[2] for s in steps if s[1] == "reviewer"], ["sonnet", "haiku"])  # task-2 (report) first, task-1 (code) stays on Haiku
        self.assertEqual(sum("qualityrun.py" in s[3] for s in steps), 1)  # the code task only

    def test_report_task_gets_a_prereview_before_its_sonnet_reviewer(self):
        from tests.test_report_tasks import two_task_project

        _, steps, _ = dry_run(two_task_project(config={"prereview": True}))
        pre = [i for i, s in enumerate(steps) if "prereview.py" in s[3]]
        self.assertEqual(len(pre), 2)  # one per task
        self.assertTrue(any("--task task-2" in steps[i][3] for i in pre))
        first_review = [i for i, s in enumerate(steps) if s[1] == "reviewer"][0]
        self.assertLess(max(pre), first_review)  # the report task's pre-review runs before its Sonnet review


class PipelineHomeTest(unittest.TestCase):
    def test_pipeline_home_env_moves_every_derived_path(self):
        code = "from pipeline import configuration as c; print(c.TOOLS_DIR, c.AGENTS_DIR, c.tool_cmd('qrun.py'))"
        tools = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        r = subprocess.run(
            [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {tools!r}); {code}"],
            capture_output=True,
            text=True,
            env={"PIPELINE_HOME": "/somewhere/else", "PATH": os.environ["PATH"]},
            check=False,
        )
        self.assertEqual(r.stdout.split(), ["/somewhere/else/tools", "/somewhere/else/agents", "python3", "-I", "/somewhere/else/tools/qrun.py"])


if __name__ == "__main__":
    unittest.main()
