"""The merge gate polls pending checks and an UNKNOWN mergeability with an injected clock (no real sleep, no gh, no network)."""

import json
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock as mock
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import pr_base, pr_ops
from pipeline.configuration import CHECKS_TIMEOUT_S, CI_POLL_S


def R(rc=0, out="", err=""):
    return SimpleNamespace(returncode=rc, stdout=out, stderr=err)


class FakeClock:
    """Time moves only when the code sleeps; every sleep is recorded."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Scripted:
    """Runner: the first key that prefixes the space-joined command wins; a list is consumed in order (its last answer repeats)."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, args, cwd=None, timeout=120):
        line = " ".join(args)
        self.calls.append(line)
        for k, v in self.table.items():
            if line.startswith(k):
                q = v if isinstance(v, list) else [v]
                return q.pop(0) if len(q) > 1 else q[0]
        return R(0)


def scripted(table):
    return Scripted(table)


def view(field, value):
    return R(0, json.dumps({field: value}))


OPEN_PR = {
    "gh pr view b --json state": view("state", "OPEN"),
    "gh pr view b --json isDraft": view("isDraft", False),
    "gh pr view b --json headRefOid": view("headRefOid", "sha-b"),
}


def run_rows(*rows):
    """`gh run list` answer: one (status, conclusion) per workflow run of the PR head."""
    return R(0, json.dumps([{"name": "ci", "status": st, "conclusion": cn} for st, cn in rows]))


GREEN_RUNS = run_rows(("completed", "success"))
PENDING_RUNS = run_rows(("in_progress", ""))
RED = json.dumps([{"name": "lint", "status": "completed", "conclusion": "failure"}])


class MergeWaitsForCi(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d, ignore_errors=True)
        self.clock = FakeClock()

    def merge(self, table, cfg=None):
        run = scripted({**OPEN_PR, **table})
        with mock.patch.object(pr_base, "_run", run):
            note = pr_ops._merge(self.d, self.d, cfg or {}, "s", "b", self.clock)
        return note, run

    def test_pending_checks_and_unknown_mergeable_are_polled_then_merged(self):
        table = {
            "gh pr view b --json mergeable": [view("mergeable", "UNKNOWN"), view("mergeable", "MERGEABLE")],
            "gh run list": [PENDING_RUNS, PENDING_RUNS, GREEN_RUNS],
            "gh pr merge b": R(0),
        }
        note, run = self.merge(table)
        self.assertEqual(note, "PR squash-merged: chore(s): s")
        self.assertEqual(self.clock.sleeps, [CI_POLL_S, CI_POLL_S, CI_POLL_S])
        self.assertEqual(sum(1 for c in run.calls if c.startswith("gh pr merge b")), 1)

    def test_red_checks_block_without_merge(self):
        table = {
            "gh pr view b --json mergeable": view("mergeable", "MERGEABLE"),
            "gh run list": R(0, RED),
        }
        with self.assertRaisesRegex(pr_ops.Blocked, "checks of b are not green \\(lint\\)"):
            self.merge(table)
        self.assertEqual(self.clock.sleeps, [])

    def test_failed_run_list_blocks_merge_not_read_as_no_checks(self):
        table = {"gh pr view b --json mergeable": view("mergeable", "MERGEABLE"), "gh run list": R(1, "", "HTTP 503")}
        with self.assertRaisesRegex(pr_ops.Blocked, "gh run list failed"):
            self.merge(table)
        self.assertEqual(self.clock.sleeps, [])

    def test_ready_blocks_on_failed_run_list(self):
        run = scripted({**OPEN_PR, "gh run list": R(1, "", "HTTP 503"), "gh pr ready": R(0)})
        with mock.patch.object(pr_base, "_run", run), self.assertRaisesRegex(pr_ops.Blocked, "gh run list failed"):
            pr_ops._ready(self.d, {}, "b")

    def test_pending_checks_past_the_timeout_block(self):
        table = {"gh pr view b --json mergeable": view("mergeable", "MERGEABLE"), "gh run list": PENDING_RUNS}
        with self.assertRaisesRegex(pr_ops.Blocked, "still pending after the wait"):
            self.merge(table, {"pr_checks_timeout": 60})
        self.assertEqual(sum(self.clock.sleeps), 60)
        self.assertEqual(self.clock.sleeps[0], CI_POLL_S)

    def test_unknown_mergeable_past_the_timeout_blocks(self):
        table = {"gh pr view b --json mergeable": view("mergeable", "UNKNOWN"), "gh run list": GREEN_RUNS}
        with self.assertRaisesRegex(pr_ops.Blocked, "of unknown mergeability"):
            self.merge(table, {"pr_checks_timeout": 60})
        self.assertEqual(sum(self.clock.sleeps), 60)

    def test_timeout_and_interval_come_from_configuration(self):
        self.assertEqual(CHECKS_TIMEOUT_S, 1800)
        self.assertEqual(CI_POLL_S, 10)
        self.assertEqual(pr_ops.checks_timeout({}), CHECKS_TIMEOUT_S)
        self.assertEqual(pr_ops.checks_timeout({"pr_checks_timeout": 5}), 5.0)


if __name__ == "__main__":
    unittest.main()
