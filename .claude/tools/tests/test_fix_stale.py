"""fix_step brief: a failing report older than the stage head asks for a rerun, not BLOCKED."""

import argparse
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import audit_phases
from pipeline.configuration import STALE_REPORT_NOTE


class StaleReportBriefTest(unittest.TestCase):
    def test_the_brief_tells_the_architect_to_compare_heads_and_retry(self):
        a = argparse.Namespace(stage="s1", dir=".")
        with mock.patch.object(audit_phases, "leads_for", return_value=""), mock.patch.object(audit_phases, "read_head", return_value=""):
            st = audit_phases.fix_step(a, [{"id": "task-1", "wave": 1}], "fix-plan-test", ["r.md"], "it failed.", "k", "why")
        self.assertIn(STALE_REPORT_NOTE.format(retry="X").split("X")[0], st["prompt"])
        self.assertIn("retry --stage s1 --step final-test", st["prompt"])
        self.assertIn("HEAD", st["prompt"])


if __name__ == "__main__":
    unittest.main()
