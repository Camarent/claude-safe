"""Auditor remit: the Plan.md acceptance checklist reaches each auditor only with the items in its remit (configuration AUDITOR_REMIT, pipeline.json `auditor_remit`)."""

import argparse
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import audit_phases
from pipeline.configuration import AUDITOR_REMIT

PLAN = """# Plan
## Goal
x
## Acceptance checklist
- cmd: scripts/check-secrets.sh -> exit 0 (secrets)
- cmd: npm audit -> no high dependency advisory
- cmd: test -f docs/rare.md -> rare readings locked (content UX goal)
- cmd: grep -c duplicat src -> architecture code health check
## Notes
- not a checklist line
"""


class RemitTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = t.name
        os.makedirs(os.path.join(self.d, ".work", "f"))
        with open(os.path.join(self.d, ".work", "f", "Plan.md"), "w", encoding="utf-8") as f:
            f.write(PLAN)
        self.a = argparse.Namespace(stage="f", dir=self.d)

    def test_sec_gets_only_sec_items(self):
        out = audit_phases.checklist_clause(self.a, {}, "sec-auditor")
        self.assertIn("scripts/check-secrets.sh", out)
        self.assertIn("npm audit", out)
        self.assertNotIn("rare readings", out)
        self.assertNotIn("duplicat", out)

    def test_arch_gets_only_arch_items(self):
        out = audit_phases.checklist_clause(self.a, {}, "arch-auditor")
        self.assertIn("duplicat", out)
        self.assertNotIn("npm audit", out)
        self.assertNotIn("rare readings", out)

    def test_out_of_remit_item_is_never_listed(self):
        for ag in ("sec-auditor", "arch-auditor"):
            self.assertNotIn("rare readings", audit_phases.checklist_clause(self.a, {}, ag))

    def test_role_without_remit_gets_whole_checklist(self):
        out = audit_phases.checklist_clause(self.a, {}, "ux-reviewer")
        self.assertIn("report every item", out)
        self.assertIn(os.path.join(".work", "f", "Plan.md"), out)

    def test_pipeline_json_remit_overrides_default(self):
        cfg = {"auditor_remit": {"sec-auditor": ["content"]}}
        out = audit_phases.checklist_clause(self.a, cfg, "sec-auditor")
        self.assertIn("rare readings", out)
        self.assertNotIn("npm audit", out)

    def test_no_item_in_remit_says_so(self):
        cfg = {"auditor_remit": {"sec-auditor": ["nothing-matches-this"]}}
        out = audit_phases.checklist_clause(self.a, cfg, "sec-auditor")
        self.assertIn("has no item in your remit", out)
        self.assertNotIn("cmd:", out)

    def test_no_plan_or_no_section_gives_empty_clause(self):
        other = argparse.Namespace(stage="g", dir=self.d)
        self.assertEqual(audit_phases.checklist_clause(other, {}, "sec-auditor"), "")

    def test_default_remit_covers_roles_in_agents(self):
        self.assertEqual(set(AUDITOR_REMIT), {"sec-auditor", "arch-auditor"})

    def test_checklist_items_parse_only_section_bullets(self):
        items = audit_phases.checklist_items(PLAN)
        self.assertEqual(len(items), 4)
        self.assertTrue(all(i.startswith("- cmd:") for i in items))


class RemitSeverityTest(unittest.TestCase):
    """A FIXES report keeps its findings when a severity word other than FAIL carries them (sec-auditor ranks critical/high/medium/low)."""

    ITEMS = ["- Docs are rendered and readable on mobile layout", "- No secrets in history"]
    TOPICS = ("secret", "injection")

    def test_severity_word_without_fail_is_severe(self):
        report = "- FAIL: Docs are rendered and readable on mobile layout\nHIGH: command injection in run.sh, must fix\n"
        ignored, severe = audit_phases.out_of_remit_split(report, self.ITEMS, self.TOPICS)
        self.assertEqual(ignored, ["- FAIL: Docs are rendered and readable on mobile layout"])
        self.assertEqual(severe, ["HIGH: command injection in run.sh, must fix"])

    def test_only_out_of_remit_fail_stays_ignored(self):
        report = "- FAIL: Docs are rendered and readable on mobile layout\nall good otherwise\n"
        ignored, severe = audit_phases.out_of_remit_split(report, self.ITEMS, self.TOPICS)
        self.assertEqual(len(ignored), 1)
        self.assertEqual(severe, [])


if __name__ == "__main__":
    unittest.main()
