"""Docs freshness audit: one Haiku freshness-auditor pass per feature, in its finish phase, in the first batch; FIXES goes to the fix round."""

import argparse
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import audit_phases, plan_files
from pipeline.configuration import ADD_DIR_AGENTS, AUDITOR_ESCALATE, AUDITORS, EV_REPORT, FIXES, FRESHNESS_AUDITOR, MODEL_HAIKU

FINISH = "pipeline-addendum/phases/03-finish"
MID = "pipeline-addendum/phases/02-build"
CFG = {"docs_fresh_audit": True, "branch_release": "master", "branch_work": "dev", "docs_dir": "docs", "audit_file": "AUDIT.md"}
FLAGS = {"effort": "medium", "ui": False, "why": ""}


def first(stage, cfg=CFG):
    a = argparse.Namespace(dir="/x", stage=stage)
    with mock.patch.object(audit_phases, "sec_audit_needed", return_value=False):
        return audit_phases._first_audits(a, cfg, "/x", False)


class FirstAuditsTest(unittest.TestCase):
    def test_finish_stage_yields_fresh_audit(self):  # (a)
        self.assertIn(("audit-fresh", "freshness-auditor"), first(FINISH))
        self.assertTrue(plan_files.is_finish_dir("03-finish"))

    def test_mid_stage_does_not(self):  # (b)
        self.assertNotIn(FRESHNESS_AUDITOR, [ag for _, ag in first(MID)])

    def test_switch_off_does_not(self):  # (c)
        self.assertNotIn(FRESHNESS_AUDITOR, [ag for _, ag in first(FINISH, {**CFG, "docs_fresh_audit": False})])

    def test_model_is_haiku_whatever_the_flags(self):
        for flags in ({"effort": "medium", "why": ""}, {"effort": "high", "why": "x"}):
            with self.subTest(flags=flags):
                self.assertEqual(audit_phases._audit_effort_model(flags, FRESHNESS_AUDITOR, "", False)[1], MODEL_HAIKU)
                self.assertEqual(audit_phases._audit_effort_model(flags, FRESHNESS_AUDITOR, "-2", False)[1], MODEL_HAIKU)


class FirstBatchTest(unittest.TestCase):
    def test_fresh_audit_joins_first_batch_without_escalation(self):
        a = argparse.Namespace(dir="/x", stage=FINISH)
        seen = []

        def fake_step(a, cfg, flags, _has_md, small, k, ag, ok, *rest):
            seen.append((k, ag, ok))
            return {"id": k}

        with mock.patch.object(audit_phases, "_audit_step", side_effect=fake_step):
            batch = next(iter(audit_phases._audit_passes(a, CFG, FLAGS, False, False, [], first(FINISH))))
        self.assertEqual([x["id"] for x in batch], ["audit-fresh", "audit-arch"])
        fresh = next(s for s in seen if s[0] == "audit-fresh")
        self.assertNotIn("escalate", str(fresh[2]).lower())  # the auditor never answers ESCALATE
        self.assertNotIn(FRESHNESS_AUDITOR, AUDITOR_ESCALATE)


class FixRoundTest(unittest.TestCase):
    def test_fixes_starts_a_fix_round(self):  # (d)
        with tempfile.TemporaryDirectory() as t:
            a = argparse.Namespace(dir=t, stage=FINISH)
            evs = [{"task": "audit-fresh", "ev": EV_REPORT, "verdict": FIXES}]
            tasks = [{"wave": 1, "owner_sonnet": False, "review_sonnet": False}]
            with (
                mock.patch.object(audit_phases, "audit_dir_of", return_value=t),
                mock.patch.object(audit_phases, "sec_audit_needed", return_value=False),
                mock.patch.object(audit_phases, "_audit_tool_phases", return_value=iter(())),
                mock.patch.object(audit_phases, "_audit_passes", return_value=iter(())),
                mock.patch.object(audit_phases, "_fix_phases", return_value=iter(())) as fix,
            ):
                list(audit_phases.audit_rounds(a, CFG, FLAGS, tasks, evs))
        bad = fix.call_args.args[7]
        self.assertIn(("audit-fresh", FRESHNESS_AUDITOR), bad)


class RegistrationTest(unittest.TestCase):
    def test_registered_as_read_only_auditor(self):  # (e)
        self.assertIn(FRESHNESS_AUDITOR, AUDITORS)
        self.assertIn(FRESHNESS_AUDITOR, ADD_DIR_AGENTS)
        self.assertIn("pl-" + FRESHNESS_AUDITOR, ADD_DIR_AGENTS)
        self.assertNotIn(FRESHNESS_AUDITOR, AUDITOR_ESCALATE)
        self.assertNotIn("pl-" + FRESHNESS_AUDITOR, AUDITOR_ESCALATE)

    def test_prompt_names_diff_docs_and_report(self):
        with tempfile.TemporaryDirectory() as t:
            a = argparse.Namespace(dir=t, stage=FINISH)
            clause = audit_phases._freshness_clause(a, CFG, FRESHNESS_AUDITOR)
        self.assertIn("git diff master...dev", clause)
        self.assertIn("`docs`", clause)
        self.assertEqual(audit_phases._freshness_clause(a, CFG, "sec-auditor"), "")


if __name__ == "__main__":
    unittest.main()
