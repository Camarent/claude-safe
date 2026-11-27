"""pipeline/metrics.py: a dispatch with no session row is counted as a cost undercount; script-role dispatches are not."""

import argparse
import contextlib
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline import metrics
from pipeline.claude import SUMS
from pipeline.ctx import Ctx
from pipeline.util import append_jsonl, read_jsonl
from tests.support.tmpdirs import tracked_mkdtemp


def dispatch(id, role="dev", task="t1"):
    return {"ev": "dispatch", "id": id, "task": task, "role": role, "agent": role, "model": "haiku"}


def usage(cost=0.5):
    return dict.fromkeys(SUMS, 1) | {"cost_usd": cost, "error": False, "sid": "sess-1"}


class MissingSessionsTest(unittest.TestCase):
    def setUp(self):
        self.d = tracked_mkdtemp()
        os.makedirs(os.path.join(self.d, ".work", "s"))
        self.c = Ctx(argparse.Namespace(stage="s", dir=self.d))
        # orch.jsonl: a1 has a session row; a53 and a246 (ops shape) have none; s9 is a script-role dispatch.
        for e in (dispatch("a1"), dispatch("a53"), dispatch("a246", role="ops"), dispatch("s9", role="script")):
            append_jsonl(self.c.log, e)
        metrics.session_line(self.c, "a1", "dev", "haiku", usage())

    def test_missing_sessions_lists_dispatches_without_a_session_row(self):
        rows = read_jsonl(self.c.metrics)
        self.assertEqual(metrics.missing_sessions(read_jsonl(self.c.log), rows), ["a53", "a246"])

    def test_total_line_prints_the_count_when_some_are_missing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            metrics.total_line(self.c)
        self.assertEqual(out.getvalue(), "no session event: 2 dispatches (cost undercount)\n")

    def test_total_line_prints_nothing_when_none_are_missing(self):
        for id in ("a53", "a246"):
            metrics.session_line(self.c, id, "dev", "haiku", usage(0))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            metrics.total_line(self.c)
        self.assertEqual(out.getvalue(), "")

    def test_script_role_dispatch_needs_no_session(self):
        only_script = [dispatch("s9", role="script")]
        self.assertEqual(metrics.missing_sessions(only_script, []), [])

    def test_existing_rows_are_not_edited_by_total_line(self):
        with open(self.c.metrics, encoding="utf-8") as f:
            before = f.read()
        with contextlib.redirect_stdout(io.StringIO()):
            metrics.total_line(self.c)
        with open(self.c.metrics, encoding="utf-8") as f:
            after = f.read()
        self.assertTrue(after.startswith(before))


if __name__ == "__main__":
    unittest.main()
