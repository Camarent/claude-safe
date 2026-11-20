"""Audit cost caps: auditor sessions get --max-turns, and a FIXES caused only by out-of-remit checklist FAILs is CLEAN."""

import argparse
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import claude, cli_orch
from pipeline.configuration import AUDITOR_MAX_TURNS, project

PLAN = "## Acceptance checklist\n- scripts/check-secrets.sh exits 0 (secrets)\n- rare readings locked in docs (content goal)\n## Notes\n"
REPORT = "VERDICT: FIXES\n- rare readings locked in docs (content goal): FAIL docs/x.md:3\n- scripts/check-secrets.sh exits 0 (secrets): PASS\n"


class CapTest(unittest.TestCase):
    def test_max_turns_arg(self):
        self.assertIn("--max-turns", claude.claude_args("sec-auditor", None, None, "p", max_turns=7))
        self.assertNotIn("--max-turns", claude.claude_args("sec-auditor", None, None, "p"))

    def test_setting_default_and_override(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(project(d)["auditor_max_turns"], AUDITOR_MAX_TURNS)
            with open(os.path.join(d, "pipeline.json"), "w", encoding="utf-8") as f:
                json.dump({"auditor_max_turns": 9}, f)
            self.assertEqual(project(d)["auditor_max_turns"], 9)


class RemitVerdictTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.d = t.name
        os.makedirs(os.path.join(self.d, ".work", "f"))
        with open(os.path.join(self.d, ".work", "f", "Plan.md"), "w", encoding="utf-8") as f:
            f.write(PLAN)
        self.a = argparse.Namespace(dir=self.d, stage="f")
        self.dsp = {"role": "sec-auditor"}

    def test_out_of_remit_fail_ignored(self):
        v, ign = cli_orch._remit_verdict(self.a, self.dsp, "FIXES", REPORT)
        self.assertEqual(v, "CLEAN")
        self.assertEqual(len(ign), 1)

    def test_real_finding_keeps_fixes(self):
        v, ign = cli_orch._remit_verdict(self.a, self.dsp, "FIXES", REPORT + "should-fix: hard-coded token in a.py:4\n")
        self.assertEqual((v, ign), ("FIXES", []))

    def test_role_without_remit_untouched(self):
        self.assertEqual(cli_orch._remit_verdict(self.a, {"role": "ux-reviewer"}, "FIXES", REPORT), ("FIXES", []))


if __name__ == "__main__":
    unittest.main()
