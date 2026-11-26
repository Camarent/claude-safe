"""planning_lines: plan-step events in metrics.jsonl become status lines in step order (synthetic events, temp dir)."""

import argparse
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from pipeline import metrics, progress
from pipeline.ctx import Ctx


def at(ts):
    return time.strftime("%H:%M:%S", time.localtime(ts))


class PlanSteps(unittest.TestCase):
    def test_lines(self):
        with tempfile.TemporaryDirectory() as d:
            c = Ctx(argparse.Namespace(stage="feat", dir=d))
            os.makedirs(os.path.dirname(c.metrics), exist_ok=True)
            self.assertEqual(progress.planning_lines(c), [])
            clock = iter([1000, 1040, 1040, 1100, 1107])
            with mock.patch.object(metrics, "now", side_effect=lambda: next(clock)):
                metrics.event(c, "plan-step", step="context", state="start")
                metrics.event(c, "plan-step", step="context", state="done", secs=40, local_model_secs=30, code_files=12)
                metrics.event(c, "plan-step", step="plan", state="start", model="sonnet")
                metrics.event(c, "plan-step", step="plan-review", state="start", kind="single")
                metrics.event(c, "plan-step", step="plan-review", state="done", verdict="DONE")
            lines = progress.planning_lines(c)
            self.assertEqual(lines[0], f"   plan/context: done (started={at(1000)} secs=40 local_model_secs=30 code_files=12)")
            self.assertEqual(lines[1], f"   plan/plan: running (started={at(1040)} model=sonnet)")
            self.assertEqual(lines[2], f"   plan/plan-review: done (started={at(1100)} secs=7 verdict=DONE)")


if __name__ == "__main__":
    unittest.main()
