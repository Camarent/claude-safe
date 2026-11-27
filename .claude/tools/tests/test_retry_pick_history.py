"""history() after `retry --from review`: the rebase record `retry_refresh` that follows the marker has no verdict, so it must not end the task's history (next_step read its missing verdict as a KeyError)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.steps_history import history
from test_task_gates import TASK, _SeqBase, dev, done, ev


def refresh():
    return ev("retry_refresh", rebased=True, discarded=False)


def review_retry():
    return ev("retry", note="retry of task-1", **{"from": "review"})


class RetryRefreshHistoryTest(_SeqBase):
    def test_next_step_after_retry_from_review_ignores_refresh(self):
        # the marker and its rebase record come after the dev DONE report: the decision must match the one without them
        evs = [dev("a1"), done("a1"), review_retry(), refresh()]
        with_refresh = self.nxt(history(evs, TASK["id"]))
        without = self.nxt([dev("a1"), done("a1")])
        self.assertEqual(with_refresh, without)

    def test_history_ends_on_last_verdict_event_after_review_retry(self):
        evs = [dev("a1"), done("a1"), review_retry(), refresh()]
        last = history(evs, TASK["id"])[-1]
        self.assertEqual((last["ev"], last["verdict"]), ("report", "DONE"))

    def test_refresh_without_retry_marker_is_kept(self):
        # decoy: no retry marker, so history() returns the task's events unchanged, the rebase record included
        evs = [dev("a1"), refresh(), done("a1")]
        self.assertEqual(history(evs, TASK["id"]), evs)


if __name__ == "__main__":
    unittest.main()
