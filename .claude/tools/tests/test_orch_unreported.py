import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import cli_orch, verdicts
from pipeline.util import read_jsonl, unreported
from tests.support.tmpdirs import tracked_mkdtemp


def dispatch(i, task):
    return {"ev": "dispatch", "id": i, "task": task, "role": "dev"}


class UnreportedTest(unittest.TestCase):
    def test_dispatch_without_report_is_pending(self):
        self.assertEqual(unreported([dispatch("a1", "alpha")]), ["a1"])

    def test_report_settles_dispatch(self):
        evs = [dispatch("a1", "alpha"), {"ev": "report", "id": "a1", "task": "alpha"}]
        self.assertEqual(unreported(evs), [])

    def test_later_dispatch_of_same_task_clears_stale_one(self):
        # a stale dispatch then a request dispatch of the same task: only the newer one may stay pending
        evs = [dispatch("a1", "alpha"), dispatch("a2", "alpha")]
        self.assertEqual(unreported(evs), ["a2"])

    def test_retry_and_skip_clear_dispatch(self):
        self.assertEqual(unreported([dispatch("a1", "alpha"), {"ev": "retry", "task": "alpha"}]), [])
        self.assertEqual(unreported([dispatch("a1", "alpha"), {"ev": "skip", "task": "alpha"}]), [])

    def test_other_task_does_not_clear(self):
        self.assertEqual(unreported([dispatch("a1", "alpha"), {"ev": "retry", "task": "beta"}]), ["a1"])


class KilledRunRetryTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        self.log = os.path.join(self.d, "orch.jsonl")
        self.disp = {"id": "a1", "task": "alpha", "role": "dev"}

    def test_rc_minus_reason_is_unclear(self):
        self.assertTrue(verdicts.is_unclear("wave: claude rc=-9: killed"))
        self.assertFalse(verdicts.is_unclear("VERDICT line missing in a FAIL"))

    def test_rc_minus_report_is_retried_and_counted(self):
        reason = "wave: claude rc=-9: killed"
        self.assertTrue(cli_orch._auto_retry(self.log, [], self.disp, reason))
        retries = [e for e in read_jsonl(self.log) if e["ev"] == "retry"]
        self.assertEqual([(e["auto"], e["task"]) for e in retries], [(True, "alpha")])
        metrics = read_jsonl(os.path.join(self.d, cli_orch.METRICS_LOG))
        self.assertEqual([(m["ev"], m["reason"]) for m in metrics], [("auto_retry", reason)])
        # counted: a second killed run of the same task and role is not retried again
        self.assertFalse(cli_orch._auto_retry(self.log, read_jsonl(self.log), self.disp, reason))


if __name__ == "__main__":
    unittest.main()
