"""Auditor remit: the Plan.md acceptance checklist reaches each auditor only with the items in its remit (configuration AUDITOR_REMIT, pipeline.json `auditor_remit`)."""

import argparse
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import audit_phases, cli_orch
from pipeline.configuration import AUDITOR_REMIT
from pipeline.configuration.verdicts import CLEAN, FIXES

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


ROADMAP = "# Roadmap\n- Docs are rendered and readable on mobile layout (open)\n- Blocker text: command injection in run.sh\n"


class RoadmapSplitTest(unittest.TestCase):
    """A should-fix line that repeats an open roadmap item is listed; FAIL and blocker lines never are."""

    def test_should_fix_in_roadmap_is_listed(self):
        report = "- should-fix: Docs are rendered and readable on mobile layout\n"
        listed, severe = audit_phases.roadmap_split(report, ROADMAP)
        self.assertEqual(listed, ["- should-fix: Docs are rendered and readable on mobile layout"])
        self.assertEqual(severe, listed)

    def test_unlisted_should_fix_stays_severe(self):
        report = "- should-fix: Docs are rendered and readable on mobile layout\n- should-fix: new unrelated finding\n"
        listed, severe = audit_phases.roadmap_split(report, ROADMAP)
        self.assertEqual(len(listed), 1)
        self.assertEqual(len(severe), 2)

    def test_blocker_line_is_never_listed(self):
        report = "- blocker: Blocker text: command injection in run.sh\n"
        listed, severe = audit_phases.roadmap_split(report, ROADMAP)
        self.assertEqual(listed, [])
        self.assertEqual(len(severe), 1)

    def test_fail_line_is_never_listed(self):
        report = "- FAIL: Docs are rendered and readable on mobile layout\n"
        listed, severe = audit_phases.roadmap_split(report, ROADMAP)
        self.assertEqual(listed, [])
        self.assertEqual(len(severe), 1)

    def test_should_fix_tagged_fail_is_never_listed(self):
        report = "- should-fix (FAIL): Docs are rendered and readable on mobile layout\n"
        listed, severe = audit_phases.roadmap_split(report, ROADMAP)
        self.assertEqual(listed, [])
        self.assertEqual(len(severe), 1)

    def test_should_fix_tagged_blocker_is_never_listed(self):
        report = "- should-fix (blocker): Docs are rendered and readable on mobile layout\n"
        listed, severe = audit_phases.roadmap_split(report, ROADMAP)
        self.assertEqual(listed, [])
        self.assertEqual(len(severe), 1)


class RemitRoadmapVerdictTest(unittest.TestCase):
    """_remit_verdict: FIXES becomes CLEAN only when pipeline.json roadmap_file names a file that lists every severe line."""

    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = t.name
        self.a = argparse.Namespace(stage="f", dir=self.d)
        self.dispatch = {"role": "sec-auditor", "task": "audit-sec"}

    def _config(self, roadmap_file=None):
        cfg = {} if roadmap_file is None else {"roadmap_file": roadmap_file}
        with open(os.path.join(self.d, "pipeline.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f)
        with open(os.path.join(self.d, "ROADMAP.md"), "w", encoding="utf-8") as f:
            f.write(ROADMAP)

    def test_roadmap_only_fixes_is_clean_with_note(self):
        self._config("ROADMAP.md")
        body = "- should-fix: Docs are rendered and readable on mobile layout\n"
        v, ignored = cli_orch._remit_verdict(self.a, self.dispatch, FIXES, body)
        self.assertEqual(v, CLEAN)
        self.assertEqual(ignored, ["- should-fix: Docs are rendered and readable on mobile layout"])

    def test_unlisted_should_fix_keeps_fixes(self):
        self._config("ROADMAP.md")
        body = "- should-fix: Docs are rendered and readable on mobile layout\n- should-fix: new unrelated finding\n"
        self.assertEqual(cli_orch._remit_verdict(self.a, self.dispatch, FIXES, body), (FIXES, []))

    def test_blocker_on_roadmap_text_keeps_fixes(self):
        self._config("ROADMAP.md")
        body = "- blocker: Blocker text: command injection in run.sh\n"
        self.assertEqual(cli_orch._remit_verdict(self.a, self.dispatch, FIXES, body), (FIXES, []))

    def test_without_key_keeps_today_behaviour(self):
        self._config()
        body = "- should-fix: Docs are rendered and readable on mobile layout\n"
        self.assertEqual(cli_orch._remit_verdict(self.a, self.dispatch, FIXES, body), (FIXES, []))

    def test_missing_roadmap_file_keeps_fixes(self):
        self._config("NOPE.md")
        body = "- should-fix: Docs are rendered and readable on mobile layout\n"
        self.assertEqual(cli_orch._remit_verdict(self.a, self.dispatch, FIXES, body), (FIXES, []))

    def test_roadmap_file_outside_repo_is_not_read(self):
        body = "- should-fix: Docs are rendered and readable on mobile layout\n"
        with tempfile.TemporaryDirectory() as outside:
            with open(os.path.join(outside, "ROADMAP.md"), "w", encoding="utf-8") as f:
                f.write(ROADMAP)
            for rel in (os.path.join(outside, "ROADMAP.md"), os.path.join("..", os.path.basename(outside), "ROADMAP.md")):
                self._config(rel)
                self.assertEqual(cli_orch._remit_verdict(self.a, self.dispatch, FIXES, body), (FIXES, []), rel)


if __name__ == "__main__":
    unittest.main()
