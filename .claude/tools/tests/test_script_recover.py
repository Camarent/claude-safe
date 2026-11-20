"""A driver that died in a script step (test run, pre-review) gets its step re-run without restarting the task: the dev and reviewer history stays."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.configuration import EV_DISPATCH, EV_REPORT, EV_RETRY
from pipeline.steps import history


def ev(name, **kw):
    return {"ev": name, "task": "task-3", **kw}


class ScriptRecoverHistory(unittest.TestCase):
    def test_script_retry_keeps_dev_report(self):
        evs = [
            ev(EV_DISPATCH, id="a1", role="dev", agent="dev"),
            ev(EV_REPORT, id="a1", role="dev", verdict="DONE"),
            ev(EV_DISPATCH, id="a2", role="script", agent="script", why="test run"),
            ev(EV_RETRY, id="a2", role="script", stale=True, **{"from": "script"}),
        ]
        self.assertEqual(history(evs, "task-3"), evs[:2])

    def test_script_retry_drops_only_the_lost_dispatch(self):
        evs = [
            ev(EV_DISPATCH, id="a1", role="dev", agent="dev"),
            ev(EV_REPORT, id="a1", role="dev", verdict="DONE"),
            ev(EV_DISPATCH, id="a2", role="script", agent="script", why="test run"),
            ev(EV_RETRY, id="a2", role="script", stale=True, **{"from": "script"}),
            ev(EV_DISPATCH, id="a3", role="script", agent="script", why="test run"),
        ]
        self.assertEqual(history(evs, "task-3"), evs[:2] + evs[4:])


if __name__ == "__main__":
    unittest.main()
