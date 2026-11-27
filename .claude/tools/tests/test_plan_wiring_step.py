"""Driver step orphan-check: after tools-check, a no-model script step, present only when pipeline.json sets wiring_dirs; its verdict set is {DONE}."""

import argparse
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import audit_phases
from pipeline.configuration import DONE, WIRING_DIRS_KEY

STAGE = "retro-m25/phases/02-wiring-tools-reports"
FLAGS = {"effort": "medium", "ui": False, "why": ""}
CFG = {"audit_file": "AUDIT.md"}


def ids_of(cfg):
    """Step ids of the tool phases in order, for a stage with no AUDIT.md tool block."""
    a = argparse.Namespace(dir="/x", stage=STAGE)
    with mock.patch.object(audit_phases, "audit_base", return_value="abc123"):
        return [s["id"] for batch in audit_phases._audit_tool_phases(a, cfg, FLAGS, "/x/audit", False) for s in batch]


def orphan_step(cfg):
    a = argparse.Namespace(dir="/x", stage=STAGE)
    with mock.patch.object(audit_phases, "audit_base", return_value="abc123"):
        batches = list(audit_phases._audit_tool_phases(a, cfg, FLAGS, "/x/audit", False))
    return next(s for batch in batches for s in batch if s["id"] == "orphan-check")


class OrphanCheckStepTest(unittest.TestCase):
    def test_step_present_after_tools_check_when_wiring_dirs_set(self):
        ids = ids_of({**CFG, WIRING_DIRS_KEY: ["src"]})
        self.assertIn("orphan-check", ids)
        self.assertEqual(ids.index("orphan-check"), ids.index("tools-check") + 1)

    def test_step_absent_without_wiring_dirs(self):
        self.assertNotIn("orphan-check", ids_of(CFG))

    def test_step_absent_with_empty_wiring_dirs(self):
        self.assertNotIn("orphan-check", ids_of({**CFG, WIRING_DIRS_KEY: []}))

    def test_step_is_script_with_done_verdict_set(self):
        s = orphan_step({**CFG, WIRING_DIRS_KEY: ["src"]})
        self.assertEqual(s["agent"], "script")
        self.assertIsNone(s["model"])
        self.assertEqual(s["ok"], {DONE})

    def test_step_runs_orphan_main_with_report_out(self):
        cmd = orphan_step({**CFG, WIRING_DIRS_KEY: ["src"]})["prompt"]
        self.assertIn("plan_wiring_orphans.py", cmd)
        self.assertIn(f"--out .work/{STAGE}/audits/orphans.md", cmd)
        self.assertIn("--base abc123", cmd)


if __name__ == "__main__":
    unittest.main()
