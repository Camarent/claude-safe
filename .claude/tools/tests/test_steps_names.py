"""Every module-level name of pipeline/steps.py at HEAD stays importable from pipeline.steps after the split (the moved names are re-exported)."""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import phases, steps, tasks
from pipeline.configuration import ACCEPTANCE_STEP

# public and underscore names of steps.py before the split (defs, classes, assigns and imports), pinned as they were
HEAD_NAMES = [
    "ALWAYS_SCRIPT",
    "ARCHITECT",
    "BLOCKED",
    "DIFF_SHA_LEN",
    "DIFF_SHA_RE",
    "DOCFIX_WHY",
    "DONE",
    "ENV_DRY_NO_CCN",
    "ENV_PREREVIEW_SKIP",
    "ENV_REPAIR_WHY",
    "ESCALATE",
    "EV_DISPATCH",
    "EV_MAIN_DIRT_CLEARED",
    "EV_REPORT",
    "EV_RETRY",
    "EV_SKIP",
    "EXTRA_ROUND_WHY",
    "FAIL",
    "GATES_KIND",
    "GATES_LINE",
    "GATE_FAILURES",
    "GATE_FREE_ROUNDS",
    "GATE_PLAN_ERROR",
    "GATE_VERDICT_RE",
    "MAX_ROUNDS",
    "MAX_TRIES",
    "METRICS_LOG",
    "MODEL_SONNET",
    "NO_FIX_RE",
    "PASS",
    "PHASES_DIR",
    "PLAN_ERROR",
    "PLAN_ERROR_RE",
    "PLAN_GAP_RE",
    "PLAN_NOTES_FILE",
    "PREREVIEW_DIFF_CHARS",
    "PREREVIEW_FREE_ROUNDS",
    "PREREVIEW_SKIP_ASK",
    "PREREVIEW_SKIP_REVIEWER",
    "REVIEW_EXTRA_ROUNDS",
    "RULES_FILE",
    "SCOUT",
    "SKIPPED",
    "STOP",
    "TEST_KIND",
    "VERIFY_WHY",
    "WEB_TESTER",
    "WORK_DIR",
    "_TaskState",
    "_after_dev",
    "_after_dev_other",
    "_after_plan_fix",
    "_after_retry",
    "_after_reviewer",
    "_after_script",
    "_after_task_gates_red",
    "_after_taskgates",
    "_after_test",
    "_after_verify",
    "_checks_blocked",
    "_dirty_rework",
    "_disp",
    "_disp_reviewer",
    "_docfix_dispatch",
    "_docfix_done",
    "_env_red",
    "_events_after",
    "_evidence_green",
    "_extra_round",
    "_extra_rounds_ran",
    "_failed_review_reports",
    "_failed_rounds",
    "_files_unchanged",
    "_gap_text",
    "_gate",
    "_gate_files",
    "_gate_script",
    "_gate_veto",
    "_gates_rework",
    "_is_lead_retry",
    "_is_report",
    "_judge_applies",
    "_lead_retry_prefix",
    "_main_dirt_cleared",
    "_next_research",
    "_next_step",
    "_next_webtest",
    "_no_commit_ask",
    "_no_edit_first_run",
    "_no_fix",
    "_paid_dev_retry",
    "_phase_dispatch",
    "_phase_failed_ask",
    "_plan_error",
    "_plan_fix",
    "_plan_gap",
    "_prereview_done",
    "_quality_or_review",
    "_reports",
    "_research_ask",
    "_reviewer_before",
    "_reviewer_reports",
    "_script",
    "_script_followup",
    "_taskgate_fails",
    "_taskgates_all",
    "_taskgates_script",
    "_test_run",
    "_test_run_script",
    "_to_review",
    "_tries",
    "append_event",
    "append_jsonl",
    "blocked_for_checks",
    "classify_test_run",
    "deliverable_texts",
    "driver_gates_on",
    "env_failure",
    "env_repair",
    "env_repair_kind",
    "failed_event",
    "failing_reports",
    "files_sha",
    "gate_on",
    "gates_of",
    "hint",
    "history",
    "is_report",
    "is_research",
    "is_unclear",
    "is_webtest",
    "job_diff",
    "job_dirty",
    "job_heads",
    "last_report_file",
    "last_verdict",
    "model_for",
    "next_step",
    "now",
    "ops_as_script",
    "paths",
    "paths_in",
    "plan_fix_prompt",
    "prereview_on",
    "prereview_skip",
    "project",
    "prompt_for",
    "qualityrun_on",
    "read_text",
    "repeated_pair",
    "repo_dir",
    "research_prompt",
    "resolve_stage",
    "run_phase",
    "script_ids",
    "sonnet_dev_dispatched",
    "step",
    "straight_to_sonnet",
    "task_gates_on",
    "task_type",
    "testrun_on",
    "tool_cmd",
    "uncommitted_diff",
    "verify_command",
    "webtest_prompt",
    "work_path",
    "write_text",
]


class StepsNamesTest(unittest.TestCase):
    def test_head_names_importable_from_steps(self):
        missing = [n for n in HEAD_NAMES if not hasattr(steps, n)]
        self.assertEqual(missing, [])


class _Stage:
    def __init__(self, d, stage):
        self.dir, self.stage = d, stage


def _final_ids(stage, release_and_pr=False):
    """-> the step ids of final_phases for a stage with one acceptance line and a test flag, in order.
    release_and_pr: a project release hook, PR mode and cleanup on, so version-bump and pr-ready are yielded."""
    with tempfile.TemporaryDirectory() as d:
        stage_dir = os.path.join(d, ".work", stage)
        os.makedirs(stage_dir)
        with open(os.path.join(stage_dir, "TASKS.md"), "w", encoding="utf-8") as f:
            f.write("# stage\n")
        with open(os.path.join(d, ".work", stage.split("/phases/")[0], "Plan.md"), "w", encoding="utf-8") as f:
            f.write("## Acceptance checklist\n- cmd: true\n")
        cfg = {**phases.project(d), "release_script": "release.sh"} if release_and_pr else phases.project(d)
        flags = {**tasks.parse_flags(os.path.join(d, "none.md")), "audit": True, "test": True, "index": False, "release": release_and_pr}
        with (
            mock.patch.object(phases, "project", return_value=cfg),
            mock.patch.object(phases.pr_mode, "pr_active", return_value=release_and_pr),
            mock.patch.object(phases.pr_mode, "cleanup_active", return_value=release_and_pr),
        ):
            return [st["id"] for g in phases.final_phases(_Stage(d, stage), flags, [], []) for st in g]


class AcceptanceOrderTest(unittest.TestCase):
    def test_finish_phase_runs_acceptance_before_the_audits(self):
        ids = _final_ids("feat/phases/08-finish")
        self.assertIn(ACCEPTANCE_STEP, ids)
        self.assertLess(ids.index(ACCEPTANCE_STEP), ids.index("plan-vs-changes"), ids)

    def test_plain_stage_keeps_acceptance_after_the_audits(self):
        ids = _final_ids("feat")
        self.assertIn(ACCEPTANCE_STEP, ids)
        self.assertGreater(ids.index(ACCEPTANCE_STEP), ids.index("plan-vs-changes"), ids)

    def test_plain_stage_keeps_acceptance_after_version_bump_and_pr_ready(self):
        ids = _final_ids("feat", release_and_pr=True)
        self.assertIn("version-bump", ids, ids)
        self.assertIn("pr-ready", ids, ids)
        self.assertLess(ids.index("version-bump"), ids.index(ACCEPTANCE_STEP), ids)
        self.assertLess(ids.index("pr-ready"), ids.index(ACCEPTANCE_STEP), ids)
        self.assertGreater(ids.index(ACCEPTANCE_STEP), ids.index("plan-vs-changes"), ids)

    def test_non_finish_phase_gets_no_acceptance_step(self):
        self.assertNotIn(ACCEPTANCE_STEP, _final_ids("feat/phases/03-build"))


if __name__ == "__main__":
    unittest.main()
