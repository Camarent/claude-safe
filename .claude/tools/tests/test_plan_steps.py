"""planning_lines: plan-step events in metrics.jsonl become status lines (synthetic events, temp dir)."""

import argparse
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import metrics, progress
from pipeline.ctx import Ctx


class PlanSteps(unittest.TestCase):
    def test_lines(self):
        with tempfile.TemporaryDirectory() as d:
            c = Ctx(argparse.Namespace(stage="feat", dir=d))
            os.makedirs(os.path.dirname(c.metrics), exist_ok=True)
            self.assertEqual(progress.planning_lines(c), [])
            metrics.event(c, "plan-step", step="context", state="start")
            metrics.event(c, "plan-step", step="context", state="done", secs=40, local_model_secs=30, code_files=12)
            metrics.event(c, "plan-step", step="plan", state="start", model="sonnet")
            lines = progress.planning_lines(c)
            self.assertEqual(lines[0], "   plan/context: done (secs=40 local_model_secs=30 code_files=12)")
            self.assertEqual(lines[1], "   plan/plan: running (model=sonnet)")


if __name__ == "__main__":
    unittest.main()
